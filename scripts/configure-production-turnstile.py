#!/usr/bin/env python3
"""Interactively configure production Turnstile without exposing its secret."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SECRETS_FILE = Path("/home/czh/.config/paper-research/secrets.env")
FRONTEND_FILE = ROOT / "apps" / "web" / ".env.local"
ACCESS_TOKEN_FILE = Path("/home/czh/.supabase/access-token")
DEFAULT_SUPABASE_CLI = ROOT / ".tools" / "supabase-2.116.0" / "supabase"
DEFAULT_GH_CLI = ROOT / ".tools" / "gh_2.97.0_linux_amd64" / "bin" / "gh"
TEST_SITE_KEYS = {
    "1x00000000000000000000AA",
    "2x00000000000000000000AB",
    "3x00000000000000000000FF",
}


def replace_env_values(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    pending = dict(updates)
    for line in existing:
        name = line.split("=", 1)[0] if "=" in line else ""
        if name in pending:
            output.append(f"{name}={pending.pop(name)}")
        else:
            output.append(line)
    output.extend(f"{name}={value}" for name, value in pending.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write("\n".join(output) + "\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def validate_cloudflare_secret(secret: str) -> None:
    response = httpx.post(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data={"secret": secret, "response": "paper-research-configuration-check"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    error_codes = set(payload.get("error-codes") or [])
    if "invalid-input-secret" in error_codes or "missing-input-secret" in error_codes:
        raise RuntimeError("Cloudflare rejected the Turnstile secret key")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-ref", default="bfjtyibeadcnpkasfffv")
    parser.add_argument("--repo", default="czh1302/paper-research")
    parser.add_argument(
        "--site-url", default="https://czh1302.github.io/paper-research/"
    )
    parser.add_argument("--supabase-cli", type=Path, default=DEFAULT_SUPABASE_CLI)
    parser.add_argument("--gh-cli", type=Path, default=DEFAULT_GH_CLI)
    args = parser.parse_args()

    site_key = input("Cloudflare Turnstile Site Key: ").strip()
    secret_key = getpass.getpass("Cloudflare Turnstile Secret Key (hidden): ").strip()
    if not site_key or not secret_key:
        raise RuntimeError("Both Turnstile keys are required")
    if site_key in TEST_SITE_KEYS:
        raise RuntimeError("A Cloudflare development test site key cannot be deployed")
    validate_cloudflare_secret(secret_key)

    replace_env_values(
        SECRETS_FILE,
        {"TURNSTILE_SECRET_KEY": secret_key, "TURNSTILE_TEST_MODE": "false"},
    )
    replace_env_values(FRONTEND_FILE, {"VITE_TURNSTILE_SITE_KEY": site_key})

    with tempfile.TemporaryDirectory(prefix="paper-research-turnstile-") as directory:
        env_file = Path(directory) / "turnstile.env"
        env_file.write_text(f"TURNSTILE_SECRET_KEY={secret_key}\n", encoding="utf-8")
        env_file.chmod(0o600)
        subprocess.run(
            [
                str(args.supabase_cli),
                "secrets",
                "set",
                "--env-file",
                str(env_file),
                "--project-ref",
                args.project_ref,
                "--agent",
                "no",
                "--output-format",
                "text",
            ],
            cwd=ROOT,
            check=True,
        )

    access_token = ACCESS_TOKEN_FILE.read_text(encoding="utf-8").strip()
    auth_response = httpx.patch(
        f"https://api.supabase.com/v1/projects/{args.project_ref}/config/auth",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "site_url": args.site_url,
            "uri_allow_list": ",".join(
                [
                    f"{args.site_url}**",
                    "http://localhost:5173/**",
                    "http://localhost:5174/**",
                ]
            ),
            "disable_signup": False,
            "external_anonymous_users_enabled": False,
            "external_email_enabled": True,
            "mailer_autoconfirm": False,
            "password_min_length": 8,
            "security_captcha_enabled": True,
            "security_captcha_provider": "turnstile",
            "security_captcha_secret": secret_key,
        },
        timeout=30,
    )
    auth_response.raise_for_status()

    subprocess.run(
        [
            str(args.gh_cli),
            "variable",
            "set",
            "VITE_TURNSTILE_SITE_KEY",
            "--repo",
            args.repo,
            "--body",
            site_key,
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            str(args.gh_cli),
            "workflow",
            "run",
            "pages.yml",
            "--repo",
            args.repo,
            "--ref",
            "main",
        ],
        cwd=ROOT,
        check=True,
    )
    print(
        json.dumps(
            {
                "turnstile_mode": "production",
                "supabase_auth_updated": True,
                "supabase_edge_secret_updated": True,
                "github_variable_updated": True,
                "pages_triggered": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
