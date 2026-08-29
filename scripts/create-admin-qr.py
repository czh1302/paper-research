#!/usr/bin/env python3
"""Create or promote an admin and encode a one-time Magic Link locally."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import qrcode
from paper_research.config import Settings
from qrcode.constants import ERROR_CORRECT_Q

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".artifacts" / "admin-login-qr.png"
DEFAULT_SITE_URL = "https://czh1302.github.io/paper-research/?admin=1"
ACCESS_TOKEN_FILE = Path("/home/czh/.supabase/access-token")


def auth_headers(service_role_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }


def project_ref(supabase_url: str) -> str:
    hostname = urlparse(supabase_url).hostname or ""
    suffix = ".supabase.co"
    if not hostname.endswith(suffix):
        raise RuntimeError("SUPABASE_URL is not a hosted Supabase project URL")
    return hostname.removesuffix(suffix)


def magic_link_expiry_seconds(supabase_url: str) -> int:
    if not ACCESS_TOKEN_FILE.exists():
        return 86_400
    token = ACCESS_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        return 86_400
    try:
        response = httpx.get(
            f"https://api.supabase.com/v1/projects/{project_ref(supabase_url)}/config/auth",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        return int(response.json().get("mailer_otp_exp") or 86_400)
    except (httpx.HTTPError, TypeError, ValueError):
        return 86_400


def generate_link(
    supabase_url: str,
    service_role_key: str,
    email: str,
    redirect_to: str,
) -> tuple[str, str]:
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/admin/generate_link",
        headers=auth_headers(service_role_key),
        json={"type": "magiclink", "email": email, "redirect_to": redirect_to},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    action_link = payload.get("action_link")
    user_id = (payload.get("user") or {}).get("id")
    if not action_link or not user_id:
        raise RuntimeError("Supabase did not return an action link and user ID")
    return str(action_link), str(user_id)


def grant_admin(supabase_url: str, service_role_key: str, user_id: str) -> None:
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/rest/v1/admin_users?on_conflict=user_id",
        headers={
            **auth_headers(service_role_key),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json={"user_id": user_id},
        timeout=30,
    )
    response.raise_for_status()


def write_qr(action_link: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_Q, box_size=9, border=4)
    qr.add_data(action_link)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    image.save(output)
    output.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an admin account and a local, one-time login QR code."
    )
    parser.add_argument("--email", help="Admin email; defaults to CROSSREF_MAILTO")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    settings = Settings()
    supabase_url = settings.SUPABASE_URL
    service_role_key = Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY)
    email = (args.email or settings.CROSSREF_MAILTO).strip().lower()
    if not supabase_url or not service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    if not email or email.endswith("@example.invalid"):
        raise RuntimeError("Pass a real admin email with --email or configure CROSSREF_MAILTO")
    if not args.site_url.startswith("https://"):
        raise RuntimeError("The production redirect URL must use HTTPS")

    action_link, user_id = generate_link(supabase_url, service_role_key, email, args.site_url)
    grant_admin(supabase_url, service_role_key, user_id)
    output = args.output.resolve()
    write_qr(action_link, output)

    expires_in = magic_link_expiry_seconds(supabase_url)
    created_at = datetime.now(UTC)
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(
            {
                "admin_email": email,
                "user_id": user_id,
                "created_at": created_at.isoformat(),
                "expires_at": (created_at + timedelta(seconds=expires_in)).isoformat(),
                "expires_in_seconds": expires_in,
                "single_use": True,
                "qr_path": str(output),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    metadata_path.chmod(0o600)
    print(
        json.dumps(
            {
                "admin_created_or_promoted": True,
                "admin_email": email,
                "qr_path": str(output),
                "expires_in_seconds": expires_in,
                "single_use": True,
                "raw_login_link_printed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
