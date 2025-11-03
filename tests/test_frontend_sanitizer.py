from pathlib import Path

import js2py
import pytest


_SANITIZER_JS = Path("app/static/js/lib/sanitize.js").read_text(encoding="utf-8")


def _build_ctx():
    ctx = js2py.EvalJs({})
    ctx.execute("var window = this; var globalThis = this;")
    ctx.execute(_SANITIZER_JS)
    return ctx


def test_sanitize_removes_script_blocks():
    ctx = _build_ctx()
    result = ctx.sanitizeHtmlFragment('<div><script>alert(1)</script><span>ok</span></div>')
    assert "<script" not in result.lower()
    assert '<span>ok</span>' in result


def test_sanitize_removes_event_handlers():
    ctx = _build_ctx()
    dirty = '<button onclick="alert(1)" onerror="foo">Run</button>'
    result = ctx.sanitizeHtmlFragment(dirty)
    assert 'onclick' not in result.lower()
    assert 'onerror' not in result.lower()
    assert '<button' in result


def test_sanitize_neutralises_javascript_urls():
    ctx = _build_ctx()
    dirty = '<a href="javascript:alert(1)">Click</a><img src="data:text/html;base64,aaaa">'
    result = ctx.sanitizeHtmlFragment(dirty)
    assert 'javascript:' not in result.lower()
    assert 'data:text/html' not in result.lower()
    assert 'href="#"' in result.lower()
    assert 'src="#"' in result.lower()


def test_safe_replace_sets_clean_html():
    ctx = _build_ctx()
    ctx.execute('var stub = { innerHTML: "" };')
    ctx.safeReplaceHtml(ctx.stub, '<div><script>boom</script><p>ok</p></div>')
    assert '<script' not in ctx.stub.innerHTML.lower()
    assert '<p>ok</p>' in ctx.stub.innerHTML


def test_sanitize_handles_empty_values():
    ctx = _build_ctx()
    assert ctx.sanitizeHtmlFragment(None) == ""
    assert ctx.sanitizeHtmlFragment("") == ""
    assert ctx.sanitizeHtmlFragment("Plain text") == "Plain text"
