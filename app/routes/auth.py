import secrets

import jwt
from flask import Blueprint, current_app, flash, redirect, request, session, url_for

from tt_common.sso import get_auth_login_url, get_auth_logout_url, is_safe_url

from ..authz import normalize_auth_payload
from ..extensions import db, limiter
from ..models import User
from ..sso_replay import is_replayed_sso_token

bp = Blueprint('auth', __name__)


@bp.route('/login')
def login():
    return redirect(get_auth_login_url('tt-members', request.args.get('next')))


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

    if is_replayed_sso_token(payload):
        flash('SSO-Token wurde bereits verwendet. Bitte erneut anmelden.', 'danger')
        return redirect(url_for('auth.login'))

    auth = normalize_auth_payload(payload)
    claims = auth['claims']
    auth_user_id = int(claims['sub'])
    username = (claims.get('username') or '').strip()
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

    user.sync_from_sso_claims(claims)
    db.session.commit()

    session['user_id'] = user.id
    session['auth_user_id'] = user.auth_user_id
    session['username'] = user.username
    session['user_role'] = user.service_role
    session['platform_role'] = user.platform_role
    session['display_name'] = user.display_name or user.username
    session['profile_complete'] = user.profile_complete
    session['memberships'] = auth['memberships']
    session['permissions'] = auth['permissions']
    session['role_permissions'] = auth['role_permissions']
    session['claims_json'] = claims
    session['nonce'] = secrets.token_hex(8)

    next_page = request.args.get('next')
    if next_page and is_safe_url(next_page):
        return redirect(next_page)
    return redirect(url_for('main.index'))
