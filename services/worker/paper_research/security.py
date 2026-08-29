from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(authorization|api[-_ ]?key|token|password|cookie|signature|credential|accesskeyid)"
        r"(\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{12,}\b"),
]


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(r"\1\2[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def validate_public_url(url: str, *, resolve_dns: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local network URLs are not allowed")

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(hostname)))
    except ValueError:
        if resolve_dns:
            for item in socket.getaddrinfo(hostname, None):
                addresses.add(item[4][0])

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, or reserved addresses are not allowed")
    return url


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (cleaned or "paper.pdf")[:160]
