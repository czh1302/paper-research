#!/usr/bin/env python3
"""Safely copy linked Supabase project settings into local environment files."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI = ROOT / ".tools" / "supabase-2.116.0" / "supabase"
SECRETS_FILE = Path("/home/czh/.config/paper-research/secrets.env")
FRONTEND_FILE = ROOT / "apps" / "web" / ".env.local"
ACCESS_TOKEN_FILE = Path("/home/czh/.supabase/access-token")
TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_ref")
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--crossref-mailto")
    parser.add_argument("--turnstile-test", action="store_true")
    args = parser.parse_args()
    command = [
        str(args.cli),
        "projects",
        "api-keys",
        "--project-ref",
        args.project_ref,
        "--reveal",
        "--output",
        "json",
        "--agent",
        "no",
        "--output-format",
        "text",
    ]
    keys = json.loads(subprocess.check_output(command, cwd=ROOT, text=True))
    by_name = {item.get("name"): item.get("api_key") for item in keys}
    anon = by_name.get("anon")
    service_role = by_name.get("service_role")
    if not anon or not service_role:
        raise RuntimeError("Supabase did not return the required legacy anon/service_role keys")
    project_url = f"https://{args.project_ref}.supabase.co"
    worker_updates = {
        "SUPABASE_URL": project_url,
        "SUPABASE_SERVICE_ROLE_KEY": service_role,
    }
    frontend_updates = {
        "VITE_SUPABASE_URL": project_url,
        "VITE_SUPABASE_ANON_KEY": anon,
    }
    if args.crossref_mailto:
        worker_updates["CROSSREF_MAILTO"] = args.crossref_mailto
    if args.turnstile_test:
        worker_updates.update(
            {
                "TURNSTILE_SECRET_KEY": TURNSTILE_TEST_SECRET_KEY,
                "TURNSTILE_TEST_MODE": "true",
            }
        )
        frontend_updates["VITE_TURNSTILE_SITE_KEY"] = TURNSTILE_TEST_SITE_KEY
    replace_env_values(SECRETS_FILE, worker_updates)
    replace_env_values(FRONTEND_FILE, frontend_updates)

    if args.turnstile_test:
        subprocess.run(
            [
                str(args.cli),
                "secrets",
                "set",
                f"TURNSTILE_SECRET_KEY={TURNSTILE_TEST_SECRET_KEY}",
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
        response = httpx.patch(
            f"https://api.supabase.com/v1/projects/{args.project_ref}/config/auth",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "site_url": "http://localhost:5173",
                "uri_allow_list": "http://localhost:5173/**",
                "disable_signup": False,
                "external_anonymous_users_enabled": False,
                "external_email_enabled": True,
                "mailer_autoconfirm": False,
                "password_min_length": 8,
                "security_captcha_enabled": True,
                "security_captcha_provider": "turnstile",
                "security_captcha_secret": TURNSTILE_TEST_SECRET_KEY,
            },
            timeout=30,
        )
        response.raise_for_status()
    print("Supabase URL and keys configured without displaying secret values.")
    if args.turnstile_test:
        print("Development-only Turnstile test mode configured; production deployment is blocked.")


if __name__ == "__main__":
    main()
