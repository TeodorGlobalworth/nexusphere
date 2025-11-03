from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user as _rl_current_user
from flask_babel import Babel
from app.utils import template_helpers  # noqa
import os
from sqlalchemy import text

db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
csrf = CSRFProtect()
babel = Babel()

def _rate_limit_key():
    """Return a stable limiter key per authenticated user; fallback to client IP."""
    try:
        if getattr(_rl_current_user, 'is_authenticated', False):
            return f"user:{getattr(_rl_current_user, 'id', 'anon')}"
    except Exception:
        pass
    return get_remote_address()

limiter = Limiter(key_func=_rate_limit_key, default_limits=[], headers_enabled=True)

def create_app(config_class='config.Config'):
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    
    # Initialize Babel with locale selector
    def get_locale():
        # Always return Polish for now (since we removed language detection)
        return 'pl'
    
    babel.init_app(app, locale_selector=get_locale)

    # Ensure csrf_token() is available in templates
    app.jinja_env.globals['csrf_token'] = generate_csrf

    login.login_view = 'auth.login'
    login.login_message_category = 'info'

    # Register blueprints
    from app.routes.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.routes.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.routes.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.routes.project import bp as project_bp
    app.register_blueprint(project_bp, url_prefix='/project')

    from app.routes.api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    # Optional WebSocket (flask-sock) endpoints
    try:
        from app.ws import register_ws
        register_ws(app)
    except Exception:
        pass

    # Template helpers
    app.jinja_env.globals['get_file_icon'] = template_helpers.get_file_icon
    app.jinja_env.globals['format_file_size'] = template_helpers.format_file_size
    app.jinja_env.globals['get_status_class'] = template_helpers.get_status_class
    app.jinja_env.globals['format_tokens_k'] = template_helpers.format_tokens_k

    # Per-request CSP nonce
    from flask import g

    @app.before_request
    def _generate_csp_nonce():  # noqa: D401
        """Generate a per-request CSP nonce (used for inline <script> tags securely)."""
        try:
            import secrets
            # 16 bytes -> base64 ~22 chars; strip padding
            g.csp_nonce = secrets.token_urlsafe(16).replace('-', '').replace('_', '')[:22]
        except Exception:
            g.csp_nonce = 'noncefallback'

    # Security headers
    @app.after_request
    def _set_security_headers(resp):
        try:
            # Basic hardening
            resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
            resp.headers.setdefault('X-Frame-Options', 'DENY')
            # Prefer CSP over XFO; keep both for legacy clients
            # Allow common CDNs used in templates by default; override via SECURITY_CSP env if needed
            # Note: we explicitly opt-out of auto-translation (Chrome/Google)
            # to avoid CSP-violating injected assets. If you want to allow
            # google translate, add its domains to style/img/script lists.
            strict = app.config.get('SECURITY_CSP_STRICT', False)
            # Build script-src with nonce
            try:
                from flask import g as _g
                nonce_val = getattr(_g, 'csp_nonce', None)
            except Exception:
                nonce_val = None
            nonce_fragment = f"'nonce-{nonce_val}'" if nonce_val else ''
            script_sources = ["'self'", nonce_fragment, 'https://cdn.jsdelivr.net', 'https://cdnjs.cloudflare.com']
            style_sources = ["'self'", 'https://cdn.jsdelivr.net', 'https://fonts.googleapis.com', 'https://cdnjs.cloudflare.com']
            if strict:
                # In strict mode add strict-dynamic to allow nonce-based runtime loads without listing every src
                script_sources.append("'strict-dynamic'")
            # All inline styles removed / replaced with utility classes; no need for 'unsafe-inline'
            csp_directives = [
                "default-src 'self'",
                f"script-src {' '.join([s for s in script_sources if s])}",
                f"style-src {' '.join(style_sources)}",
                "img-src 'self' data: https://api.qrserver.com",
                "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com",
                "connect-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
                "frame-ancestors 'none'",
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                ("upgrade-insecure-requests" if app.config.get('CSP_UPGRADE_INSECURE_REQUESTS', True) else ''),
            ]
            # Remove any empty directives
            csp_directives = [d for d in csp_directives if d]
            csp = app.config.get('SECURITY_CSP') or "; ".join(csp_directives)
            resp.headers.setdefault('Content-Security-Policy', csp)
            resp.headers.setdefault('Referrer-Policy', app.config.get('REFERRER_POLICY', 'strict-origin-when-cross-origin'))
            resp.headers.setdefault('Permissions-Policy', app.config.get('PERMISSIONS_POLICY', "geolocation=(), microphone=(), camera=()"))
            # HSTS (only if enabled)
            if app.config.get('ENABLE_HSTS', False):
                max_age = int(app.config.get('HSTS_MAX_AGE', 15552000))  # 180 days
                include_sub = '; includeSubDomains' if app.config.get('HSTS_INCLUDE_SUBDOMAINS', True) else ''
                preload = '; preload' if app.config.get('HSTS_PRELOAD', False) else ''
                resp.headers.setdefault('Strict-Transport-Security', f'max-age={max_age}{include_sub}{preload}')
        except Exception:
            pass
        return resp

    # CSRF error handling: return JSON for API/AJAX
    from flask_wtf.csrf import CSRFError
    from flask import jsonify, request, flash, redirect, url_for

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        wants_json = request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
        if wants_json:
            return jsonify({'success': False, 'error': 'CSRF validation failed'}), 400
        flash('Session expired or CSRF error. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    # Global error handlers: return JSON for API/AJAX requests
    try:
        from flask import jsonify, request, render_template
        from werkzeug.exceptions import RequestEntityTooLarge

        def _wants_json():
            try:
                return request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
            except Exception:
                return False

        @app.errorhandler(404)
        def handle_404(e):
            if _wants_json():
                return jsonify({'success': False, 'error': 'Not Found'}), 404
            try:
                return render_template('errors/404.html'), 404
            except Exception:
                return 'Not Found', 404

        @app.errorhandler(403)
        def handle_403(e):
            if _wants_json():
                return jsonify({'success': False, 'error': 'Forbidden'}), 403
            try:
                return render_template('errors/403.html'), 403
            except Exception:
                return 'Forbidden', 403

        @app.errorhandler(500)
        def handle_500(e):
            if _wants_json():
                return jsonify({'success': False, 'error': 'Server Error'}), 500
            try:
                return render_template('errors/500.html'), 500
            except Exception:
                return 'Server Error', 500

        @app.errorhandler(RequestEntityTooLarge)
        def handle_413(e):
            if _wants_json():
                return jsonify({'success': False, 'error': 'Request too large'}), 413
            try:
                return render_template('errors/413.html'), 413
            except Exception:
                return 'Request too large', 413
    except Exception:
        pass

    # Health endpoint for container/lb checks (no auth, no rate limit)
    try:
        @app.get('/healthz')
        @limiter.exempt
        def healthz():
            return 'ok', 200
    except Exception:
        pass

    # Jinja helper for CSP nonce (must be registered before returning app)
    def _csp_nonce():  # noqa: D401
        try:
            from flask import g as _g
            return getattr(_g, 'csp_nonce', '')
        except Exception:
            return ''
    app.jinja_env.globals['csp_nonce'] = _csp_nonce

    return app
