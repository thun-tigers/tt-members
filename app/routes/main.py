from functools import wraps

import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from ..extensions import db
from ..models import MemberProfile, User

bp = Blueprint('main', __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def current_user():
    user_id = session.get('user_id')
    return db.session.get(User, user_id) if user_id else None


@bp.route('/health')
def health():
    return {'status': 'ok'}, 200


@bp.route('/')
@login_required
def index():
    user = current_user()
    if not user.profile_complete or not user.profile:
        return redirect(url_for('main.profile'))
    return render_template('dashboard.html', current_user=user)


@bp.route('/submitted')
@login_required
def submitted():
    user = current_user()
    if not user.profile_complete:
        return redirect(url_for('main.profile'))
    return render_template('submitted.html', current_user=user)


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user()
    profile = user.profile
    if request.method == 'POST':
        first_name = (request.form.get('first_name') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()
        phone = (request.form.get('phone') or '').strip() or None
        notes = (request.form.get('notes') or '').strip() or None

        if not first_name or not last_name:
            flash('Vorname und Nachname sind erforderlich.', 'danger')
        else:
            if not profile:
                profile = MemberProfile(user_id=user.id, first_name=first_name, last_name=last_name)
                db.session.add(profile)
            profile.first_name = first_name
            profile.last_name = last_name
            profile.phone = phone
            profile.notes = notes
            user.first_name = first_name
            user.last_name = last_name
            user.display_name = f'{first_name} {last_name}'
            db.session.flush()
            if _notify_auth_profile_complete(user.auth_user_id):
                user.profile_complete = True
                db.session.commit()
                return redirect(url_for('main.submitted'))
            db.session.rollback()
            flash('Das Profil konnte nicht an TT-Auth übermittelt werden. Bitte erneut versuchen.', 'danger')

    return render_template(
        'profile.html',
        current_user=user,
        profile=profile,
        initial_first_name=profile.first_name if profile else (user.first_name or ''),
        initial_last_name=profile.last_name if profile else (user.last_name or ''),
    )


def _notify_auth_profile_complete(auth_user_id):
    auth_base = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return False
    try:
        response = requests.post(
            f'{auth_base}/api/users/{auth_user_id}/profile-complete',
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-auth profile sync failed: %s %s', response.status_code, response.text)
            return False
        return True
    except requests.RequestException as exc:
        current_app.logger.warning('tt-auth profile sync failed: %s', exc)
        return False
