import re
from app import create_app

def test_csp_header_no_unsafe_inline_scripts():
    app = create_app()
    client = app.test_client()
    resp = client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    # script-src should exist
    assert 'script-src' in csp
    # unsafe-inline should not appear for scripts
    assert "'unsafe-inline'" not in csp, f"CSP still has unsafe-inline: {csp}"
    # nonce should be present
    assert re.search(r"script-src [^;]*'nonce-[A-Za-z0-9]+" , csp)


def test_csp_header_style_no_unsafe_inline():
    app = create_app()
    client = app.test_client()
    resp = client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    # Ensure style-src has no unsafe-inline now
    style_part = next((p for p in csp.split(';') if 'style-src' in p), '')
    assert "'unsafe-inline'" not in style_part
