from functools import wraps
from flask import redirect, url_for, jsonify
from flask_login import current_user


def require_admin(view):
    @wraps(view)
    def _wrapped(*args, **kwargs):
        try:
            if not getattr(current_user, 'is_authenticated', False):
                return redirect(url_for('auth.login'))
            if not (getattr(current_user, 'is_admin', False) or getattr(current_user, 'is_superadmin', False)):
                return redirect(url_for('main.dashboard'))
            return view(*args, **kwargs)
        except Exception:
            return redirect(url_for('auth.login'))
    return _wrapped


def require_superadmin(view):
    @wraps(view)
    def _wrapped(*args, **kwargs):
        try:
            if not getattr(current_user, 'is_authenticated', False):
                return redirect(url_for('auth.login'))
            if not getattr(current_user, 'is_superadmin', False):
                return redirect(url_for('admin.dashboard'))
            return view(*args, **kwargs)
        except Exception:
            return redirect(url_for('auth.login'))
    return _wrapped
