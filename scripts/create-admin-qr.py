#!/usr/bin/env python3
"""Create or promote an admin and encode a reusable permanent login ticket locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import qrcode
from paper_research.config import Settings
from qrcode.constants import ERROR_CORRECT_Q

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".artifacts" / "admin-login-qr.png"
DEFAULT_SITE_URL = "https://czh1302.github.io/paper-research/"


def auth_headers(service_role_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }


def find_user_id(supabase_url: str, service_role_key: str, email: str) -> str | None:
    for page in range(1, 11):
        response = httpx.get(
            f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
            headers=auth_headers(service_role_key),
            params={"page": page, "per_page": 1000},
            timeout=30,
        )
        response.raise_for_status()
        users = response.json().get("users") or []
        for user in users:
            if str(user.get("email") or "").strip().lower() == email:
                return str(user["id"])
        if len(users) < 1000:
            break
    return None


def ensure_user(supabase_url: str, service_role_key: str, email: str) -> str:
    existing = find_user_id(supabase_url, service_role_key, email)
    if existing:
        return existing
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
        headers=auth_headers(service_role_key),
        json={"email": email, "email_confirm": True},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    user_id = payload.get("id") or (payload.get("user") or {}).get("id")
    if not user_id:
        raise RuntimeError("Supabase created the user without returning its ID")
    return str(user_id)


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
    verification = httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/admin_users",
        headers=auth_headers(service_role_key),
        params={"select": "user_id", "user_id": f"eq.{user_id}"},
        timeout=30,
    )
    verification.raise_for_status()
    if not verification.json():
        raise RuntimeError("Administrator grant could not be verified")


def create_ticket(
    supabase_url: str,
    service_role_key: str,
    user_id: str,
    valid_days: int | None,
) -> tuple[str, datetime, datetime | None]:
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=valid_days) if valid_days is not None else None
    revoke = httpx.patch(
        f"{supabase_url.rstrip('/')}/rest/v1/admin_login_tickets",
        headers={**auth_headers(service_role_key), "Prefer": "return=minimal"},
        params={
            "admin_user_id": f"eq.{user_id}",
            "consumed_at": "is.null",
            "revoked_at": "is.null",
        },
        json={"revoked_at": created_at.isoformat()},
        timeout=30,
    )
    revoke.raise_for_status()

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/rest/v1/admin_login_tickets",
        headers={**auth_headers(service_role_key), "Prefer": "return=minimal"},
        json={
            "token_hash": token_hash,
            "admin_user_id": user_id,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at is not None else "infinity",
        },
        timeout=30,
    )
    response.raise_for_status()
    verification = httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/admin_login_tickets",
        headers=auth_headers(service_role_key),
        params={"select": "expires_at", "token_hash": f"eq.{token_hash}"},
        timeout=30,
    )
    verification.raise_for_status()
    if not verification.json():
        raise RuntimeError("Administrator login ticket could not be verified")
    return token, created_at, expires_at


def build_ticket_url(site_url: str, token: str) -> str:
    parts = urlsplit(site_url)
    if parts.scheme != "https" or not parts.netloc:
        raise RuntimeError("The production site URL must use HTTPS")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, f"admin_ticket={token}"))


def write_qr(value: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_Q, box_size=9, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    image.save(output)
    output.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an admin account and a local, reusable permanent login QR code."
    )
    parser.add_argument("--email", help="Admin email; defaults to CROSSREF_MAILTO")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument(
        "--valid-days",
        type=int,
        help="Optional finite lifetime (1-30 days); omit for a permanent QR code",
    )
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
    if args.valid_days is not None and (args.valid_days < 1 or args.valid_days > 30):
        raise RuntimeError("--valid-days must be between 1 and 30")

    user_id = ensure_user(supabase_url, service_role_key, email)
    grant_admin(supabase_url, service_role_key, user_id)
    token, created_at, expires_at = create_ticket(
        supabase_url, service_role_key, user_id, args.valid_days
    )
    output = args.output.resolve()
    write_qr(build_ticket_url(args.site_url, token), output)

    expires_in = args.valid_days * 86_400 if args.valid_days is not None else None
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(
            {
                "admin_email": email,
                "user_id": user_id,
                "credential_type": "permanent_reusable_admin_ticket",
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat() if expires_at is not None else None,
                "expires_in_seconds": expires_in,
                "single_use": False,
                "reusable_until_expiry": True,
                "never_expires": expires_at is None,
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
                "valid_days": args.valid_days,
                "single_use": False,
                "reusable_until_expiry": True,
                "never_expires": expires_at is None,
                "raw_ticket_printed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
