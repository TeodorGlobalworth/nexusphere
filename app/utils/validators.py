from __future__ import annotations

import re


_SUBDOMAIN_INVALID_CHARS = re.compile(r"[^a-z0-9-]")
_SUBDOMAIN_VALID_RE = re.compile(r"^(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def sanitize_text(value: str | None, *, max_len: int = 5000, strip_ctrl: bool = True) -> str:
    if value is None:
        return ""
    s = str(value)
    if strip_ctrl:
        s = _CTRL_RE.sub(" ", s)
    s = s.strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def is_valid_email(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip()
    return bool(_EMAIL_RE.match(v))


def to_int(value, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    if minimum is not None and n < minimum:
        n = minimum
    if maximum is not None and n > maximum:
        n = maximum
    return n


def normalize_subdomain(value: str | None) -> str:
    """Normalize user-provided subdomain slug.

    Replaces unsupported characters with '-', collapses duplicates, trims
    leading/trailing hyphens and enforces the 63-char DNS label limit.
    """
    if value is None:
        return ""
    slug = str(value).strip().lower()
    if not slug:
        return ""
    slug = _SUBDOMAIN_INVALID_CHARS.sub('-', slug)
    slug = re.sub(r'-{2,}', '-', slug)
    slug = slug.strip('-')
    if len(slug) > 63:
        slug = slug[:63]
        slug = slug.rstrip('-')
    return slug


def is_valid_subdomain(slug: str | None) -> bool:
    if not slug:
        return False
    return bool(_SUBDOMAIN_VALID_RE.match(slug))
