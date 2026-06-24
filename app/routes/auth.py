import secrets
from urllib.parse import urlencode, urljoin, urlparse

import jwt
from flask import Blueprint, current_app, flash, redirect, request, session, url_for

from ..extensions import db, limiter
from ..models import User

bp = Blueprint('auth', __name__)


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def get_auth_login_url(next_page=None):
    auth_base_url = current_app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
    query = {'next_service': 'tt-members'}
    if next_page:
        query['next'] = next_page
    return f"{auth_base_url}/?{urlencode(query)}"


@bp.route('/login')
def login():
    return redirect(get_auth_login_url(request.args.get('next')))


def get_auth_logout_url():
    auth_base_url = current_app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
    return f"{auth_base_url}/logout"


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(get_auth_logout_url())


@bp.route('/auth/sso')
@limiter.limit('60/minute')
def sso_login():
    token = request.args.get('token', '').strip()
    if not token:
        flash('SSO-Token fehlt.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        payload = jwt.decode(
            token,
            current_app.config.get('SSO_SHARED_SECRET') or current_app.config.get('SECRET_KEY'),
            algorithms=['HS256'],
            audience=current_app.config.get('SSO_EXPECTED_AUDIENCE', 'tt-members'),
        )
    except jwt.ExpiredSignatureError:
        flash('SSO-Token ist abgelaufen. Bitte erneut starten.', 'warning')
        return redirect(url_for('auth.login'))
    except jwt.InvalidTokenError:
        flash('Ungültiger SSO-Token.', 'danger')
        return redirect(url_for('auth.login'))

    auth_user_id = int(payload['sub'])
    username = (payload.get('username') or '').strip()
    if not username:
        flash('SSO-Token enthält keinen Benutzernamen.', 'danger')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(auth_user_id=auth_user_id).first()
    if not user:
        # Fallback: username may already exist with a stale auth_user_id (e.g. after re-registration)
        user = User.query.filter_by(username=username).first()
        if user:
            user.auth_user_id = auth_user_id
        else:
            user = User(auth_user_id=auth_user_id, username=username)
            db.session.add(user)

    user.username = username
    user.first_name = payload.get('first_name')
    user.last_name = payload.get('last_name')
    user.display_name = payload.get('display_name') or username
    user.email = payload.get('email')
    user.platform_role = payload.get('platform_role') or 'user'
    user.service_role = payload.get('service_role') or payload.get('role') or 'user'
    user.profile_complete = bool(payload.get('profile_complete'))
    user.claims_json = payload
    db.session.commit()

    session['user_id'] = user.id
    session['auth_user_id'] = user.auth_user_id
    session['username'] = user.username
    session['platform_role'] = user.platform_role
    session['permissions'] = payload.get('permissions') or []
    session['nonce'] = secrets.token_hex(8)

    next_page = request.args.get('next')
    if next_page and is_safe_url(next_page):
        return redirect(next_page)
    return redirect(url_for('main.index'))
