
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, current_user, login_required
from app.models.user import User
from sqlalchemy import func
from app import db, limiter
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10/minute; 100/hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember = request.form.get('remember', False)

        # Case-insensitive email lookup
        email_norm = (email or '').strip().lower()
        user = User.query.filter(func.lower(User.email) == email_norm).first()
        if user and user.check_password(password):
            user.last_login = datetime.utcnow()
            db.session.commit()
            session.permanent = True
            login_user(user, remember=remember)

            next_page = request.args.get('next')
            if user.is_admin:
                return redirect(next_page) if next_page else redirect(url_for('admin.dashboard'))
            else:
                return redirect(next_page) if next_page else redirect(url_for('main.dashboard'))

    # If POST with invalid credentials, show error; for GET just render form
    if request.method == 'POST':
        try:
            norm = (request.form.get('email') or '').strip().lower()
            current_app.logger.warning('Failed login attempt for email=%s', norm)
        except Exception:
            pass
        flash("Nieprawidłowy email lub hasło", 'error')

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

