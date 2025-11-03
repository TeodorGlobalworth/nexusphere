Security hardening summary

What changed
- Added strict security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy). Optional HSTS via ENABLE_HSTS.
- Safe config proxy to templates to avoid leaking secrets.
- Stronger path traversal checks for file downloads and deletions using os.path.commonpath.
- API and project routes rate limiting on sensitive POST/DELETE endpoints.
- OAuth state now uses a cryptographically random nonce stored in the session and validated on callback.
- CSRF error handler returns JSON for AJAX/API and a friendly message for HTML.

Recommended env in production
- SESSION_COOKIE_SECURE=true
- REMEMBER_COOKIE_SECURE=true
- ENABLE_HSTS=true
- HSTS_PRELOAD=true (only after verifying HTTPS and subdomains)
- SECURITY_CSP to a tailored policy if using CDNs; otherwise default is self + inline.

Notes
- CSRF is globally enabled via Flask-WTF. Ensure frontend sends X-CSRFToken from meta tag for fetch/JSON requests.
- File uploads are validated by extension and optionally MIME sniffing (python-magic). Keep ALLOWED_EXTENSIONS minimal.

## WebSocket Controls (Planned / In Progress)
We will enforce:
- Heartbeat/idle timeout: client must send a JSON ping `{ "type": "ping" }` every 30s (server grace 90s). Idle connections will be closed to free resources.
- Per-user WebSocket connection limit: default 1 concurrent knowledge socket per user per project (superadmins exempt). Rationale: prevents resource exhaustion & tab-spam; can increase later if needed.
- Server will answer with `{ "type": "pong", "ts": <iso> }` for pings; any other small control frames ignored.
- If limit exceeded server closes the oldest connection with a policy violation code (4000) or rejects new one (tunable strategy) logging an audit line `[WS] Limit exceeded for user X`.

Configuration (to be added):
- `WS_MAX_CONNECTIONS_PER_USER` (int, default 1, 0 = unlimited)
- `WS_HEARTBEAT_INTERVAL` (seconds client send suggestion; default 30)
- `WS_IDLE_TIMEOUT` (seconds before server closes; default 90)

Superadmin Bypass:
- If `current_user.is_superadmin` is True, limits are skipped (still subject to idle timeout for cleanup).

Threats addressed:
- Tab amplification / accidental DoS
- Abandoned connections accumulating (network drops, user closes laptop)
- Ensures timely propagation and frees resources.

Audit & Metrics:
- Log connection open/close with project id & user id.
- Count active connections by user for potential Prometheus exporter later.

Implementation status:
- Implemented: config values, server heartbeat & idle close, per-user limit w/ oldest-connection eviction, client pings on relevant pages.
- Pending: optional close code 4000 (currently normal close), structured audit logging & metrics export.