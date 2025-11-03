from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from flask import current_app, request, url_for, g, has_request_context

from .validators import normalize_subdomain


@dataclass
class HostResolution:
    host: str
    port: Optional[int]
    base_domain: Optional[str]
    slug: Optional[str]
    is_primary: bool
    is_admin_host: bool
    is_localhost: bool
    is_allowed_alt: bool


def _split_host_port(raw_host: str | None) -> tuple[str, Optional[int]]:
    if not raw_host:
        return "", None
    host = raw_host.strip()
    if not host:
        return "", None
    if host.count(':') == 0:
        return host.lower(), None
    # IPv6 literal support: [::1]:5000
    if host.startswith('['):
        closing = host.find(']')
        if closing != -1:
            port_part = host[closing + 1 :]
            host_part = host[1:closing]
            try:
                port_val = int(port_part.lstrip(':')) if port_part.startswith(':') else None
            except ValueError:
                port_val = None
            return host_part.lower(), port_val
    host_part, _, port_str = host.rpartition(':')
    try:
        port_val = int(port_str)
    except ValueError:
        host_part = host
        port_val = None
    return host_part.lower(), port_val


def resolve_host(raw_host: str | None, config: dict | None = None) -> HostResolution:
    cfg = config or (current_app.config if current_app else {})
    host, port = _split_host_port(raw_host)
    base_domain = (cfg.get('PRIMARY_DOMAIN') or '').lower().strip('.') or None
    admin_subdomain = (cfg.get('ADMIN_SUBDOMAIN') or '').strip().lower() or None
    reserved = set(s.strip().lower() for s in (cfg.get('RESERVED_SUBDOMAINS') or []))
    if admin_subdomain:
        reserved.discard(admin_subdomain)  # admin handled separately
    localhost_hosts = {'localhost', '127.0.0.1', '::1'}
    additional_roots = set(s.strip().lower() for s in (cfg.get('ADDITIONAL_ROOT_HOSTS') or []))
    is_local = host in localhost_hosts
    is_allowed_alt = host in additional_roots
    slug: Optional[str] = None
    is_primary = False
    is_admin_host = False

    if not host:
        return HostResolution(host='', port=None, base_domain=base_domain, slug=None, is_primary=False, is_admin_host=False, is_localhost=False, is_allowed_alt=False)

    if base_domain and host == base_domain:
        is_primary = True
    elif base_domain and host.endswith(f'.{base_domain}'):
        slug = host[: -(len(base_domain) + 1)]
        slug = normalize_subdomain(slug)
        if admin_subdomain and slug == admin_subdomain:
            is_admin_host = True
            slug = None
        elif slug in reserved:
            # treat reserved as primary (no organization)
            slug = None
    elif host in localhost_hosts:
        is_local = True
    else:
        # host doesn't match base domain; allow only if explicitly whitelisted
        is_allowed_alt = is_allowed_alt or host in (cfg.get('ADDITIONAL_ALLOWED_HOSTS') or [])

    return HostResolution(
        host=host,
        port=port,
        base_domain=base_domain,
        slug=slug,
        is_primary=is_primary,
        is_admin_host=is_admin_host,
        is_localhost=is_local,
        is_allowed_alt=is_allowed_alt,
    )


def build_host_for_slug(slug: str | None, *, config: dict | None = None) -> str:
    cfg = config or (current_app.config if current_app else {})
    base_domain = (cfg.get('PRIMARY_DOMAIN') or '').strip()
    if not base_domain:
        return slug or ''
    if slug:
        return f"{slug}.{base_domain}"
    admin_subdomain = (cfg.get('ADMIN_SUBDOMAIN') or '').strip().lower() or None
    if slug == admin_subdomain:
        return f"{admin_subdomain}.{base_domain}"
    return base_domain


def build_external_url(slug: str | None, endpoint: str, *, config: dict | None = None, scheme: Optional[str] = None, **values) -> str:
    cfg = config or (current_app.config if current_app else {})
    scheme = scheme or cfg.get('TENANT_URL_SCHEME') or cfg.get('PREFERRED_URL_SCHEME') or 'https'
    host = build_host_for_slug(slug, config=cfg)
    path = url_for(endpoint, _external=False, **values)
    port = None
    if has_request_context():
        host_info = getattr(g, 'tenant_host', None)
        if host_info is None:
            host_info = resolve_host(request.host if request else None, cfg)
        if host_info and host_info.port:
            port = host_info.port
    if port is None:
        port = cfg.get('TENANT_PUBLIC_PORT') or cfg.get('TENANT_DEV_PORT')
    if port:
        port = str(port)
        # Avoid duplicating standard ports (80/443)
        if port in {'80', '443'}:
            port = None
    netloc = host
    if port:
        netloc = f"{host}:{port}"
    return f"{scheme}://{netloc}{path}"


def current_request_slug() -> Optional[str]:
    host_info = resolve_host(request.host if request else None)
    return host_info.slug


def is_reserved_subdomain(slug: str | None, *, config: dict | None = None) -> bool:
    if not slug:
        return False
    cfg = config or (current_app.config if current_app else {})
    reserved = set(s.strip().lower() for s in (cfg.get('RESERVED_SUBDOMAINS') or []))
    admin_subdomain = (cfg.get('ADMIN_SUBDOMAIN') or '').strip().lower() or None
    if admin_subdomain:
        reserved.add(admin_subdomain)
    return slug.lower() in reserved