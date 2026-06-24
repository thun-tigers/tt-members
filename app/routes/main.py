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


@bp.route('/team-manager')
@login_required
def team_manager():
    user = current_user()
    managed_team_codes = _managed_team_codes(user)
    if not managed_team_codes:
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    pending_users, fetch_error = _fetch_pending_users_for_manager(user)
    if fetch_error:
        flash(fetch_error, 'danger')

    return render_template(
        'team_manager.html',
        current_user=user,
        managed_team_codes=managed_team_codes,
        pending_users=pending_users,
    )


@bp.route('/team-manager/approve/<int:target_user_id>', methods=['POST'])
@login_required
def approve_pending_user(target_user_id):
    user = current_user()
    if not _managed_team_codes(user):
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    ok, error = _approve_user_via_auth(user, target_user_id)
    if ok:
        flash('Benutzer erfolgreich freigegeben.', 'success')
    else:
        flash(error or 'Freigabe fehlgeschlagen.', 'danger')
    return redirect(url_for('main.team_manager'))


@bp.route('/team-manager/reject/<int:target_user_id>', methods=['POST'])
@login_required
def reject_pending_user(target_user_id):
    user = current_user()
    if not _managed_team_codes(user):
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    reason = (request.form.get('reason') or '').strip()
    ok, error = _reject_user_via_auth(user, target_user_id, reason)
    if ok:
        flash('Antrag wurde abgelehnt.', 'success')
    else:
        flash(error or 'Ablehnung fehlgeschlagen.', 'danger')
    return redirect(url_for('main.team_manager'))


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
            already_complete = user.profile_complete
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
            if already_complete:
                # Profil-Update: kein erneuter Antrag, direkt zurück zum Dashboard
                db.session.commit()
                flash('Profil erfolgreich aktualisiert.', 'success')
                return redirect(url_for('main.index'))
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


def _managed_team_codes(user):
    memberships = (user.claims_json or {}).get('memberships', [])
    team_codes = {
        (membership.get('team_code') or '').strip().upper()
        for membership in memberships
        if membership.get('member_role') in {'team_manager', 'head_coach'} and membership.get('team_code')
    }
    return sorted(code for code in team_codes if code)


def _fetch_pending_users_for_manager(user):
    auth_base = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return [], 'INTERNAL_API_SECRET ist nicht konfiguriert.'

    try:
        response = requests.get(
            f'{auth_base}/api/team-manager/pending-users',
            params={'approver_auth_user_id': user.auth_user_id},
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-auth pending fetch failed: %s %s', response.status_code, response.text)
            return [], 'Offene Anträge konnten nicht geladen werden.'
        payload = response.json() or {}
        return payload.get('pending_users', []), None
    except requests.RequestException as exc:
        current_app.logger.warning('tt-auth pending fetch failed: %s', exc)
        return [], 'Offene Anträge konnten nicht geladen werden.'


def _approve_user_via_auth(user, target_user_id):
    auth_base = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return False, 'INTERNAL_API_SECRET ist nicht konfiguriert.'

    try:
        response = requests.post(
            f'{auth_base}/api/team-manager/approve-user',
            json={
                'approver_auth_user_id': user.auth_user_id,
                'target_user_id': target_user_id,
            },
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-auth approve failed: %s %s', response.status_code, response.text)
            if response.status_code == 403:
                return False, 'Keine Berechtigung für diese Freigabe.'
            if response.status_code == 409:
                return False, 'Antrag ist nicht mehr freigabefähig.'
            return False, 'Freigabe konnte nicht durchgeführt werden.'
        return True, None
    except requests.RequestException as exc:
        current_app.logger.warning('tt-auth approve failed: %s', exc)
        return False, 'Freigabe konnte nicht durchgeführt werden.'


def _reject_user_via_auth(user, target_user_id, reason):
    auth_base = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return False, 'INTERNAL_API_SECRET ist nicht konfiguriert.'

    try:
        response = requests.post(
            f'{auth_base}/api/team-manager/reject-user',
            json={
                'approver_auth_user_id': user.auth_user_id,
                'target_user_id': target_user_id,
                'reason': reason,
            },
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-auth reject failed: %s %s', response.status_code, response.text)
            if response.status_code == 403:
                return False, 'Keine Berechtigung für diese Ablehnung.'
            if response.status_code == 409:
                return False, 'Antrag ist nicht mehr ablehnbar.'
            return False, 'Ablehnung konnte nicht durchgeführt werden.'
        return True, None
    except requests.RequestException as exc:
        current_app.logger.warning('tt-auth reject failed: %s', exc)
        return False, 'Ablehnung konnte nicht durchgeführt werden.'
