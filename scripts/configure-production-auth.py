#!/usr/bin/env python3
"""Set canonical Supabase Auth redirects without reading or changing provider secrets."""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx

ACCESS_TOKEN_FILE = Path("/home/czh/.supabase/access-token")
DEFAULT_SITE_URL = "https://czh1302.github.io/paper-research/"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-ref", default="bfjtyibeadcnpkasfffv")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    args = parser.parse_args()
    site_url = args.site_url.rstrip("/") + "/"
    access_token = ACCESS_TOKEN_FILE.read_text(encoding="utf-8").strip()
    response = httpx.patch(
        f"https://api.supabase.com/v1/projects/{args.project_ref}/config/auth",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "site_url": site_url,
            "uri_allow_list": ",".join(
                [f"{site_url}**", "http://localhost:5173/**", "http://localhost:5174/**"]
            ),
        },
        timeout=30,
    )
    response.raise_for_status()
    print(f"Supabase Auth redirects now use {site_url}")


if __name__ == "__main__":
    main()
