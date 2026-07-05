from functools import wraps
from datetime import date
import re

import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from ..authz import has_role_permission, is_platform_admin, normalize_memberships, normalize_permissions
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


@bp.route('/antraege', endpoint='antraege')
@login_required
def antraege():
    user = current_user()
    is_platform_admin = _is_platform_admin(user)
    managed_team_codes = _managed_team_codes(user)
    if not is_platform_admin and not _has_members_manage_permission(user) and not managed_team_codes:
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    pending_users, fetch_error = _fetch_pending_users_for_manager(user)
    if fetch_error:
        flash(fetch_error, 'danger')

    return render_template(
        'team_manager.html',
        current_user=user,
        is_platform_admin=is_platform_admin,
        managed_team_codes=managed_team_codes,
        pending_users=pending_users,
    )


@bp.route('/members')
@login_required
def members():
    user = current_user()
    if not _can_view_members(user):
        flash('Kein Zugriff auf die Mitgliederverwaltung.', 'danger')
        return redirect(url_for('main.index'))

    query = (request.args.get('q') or '').strip()
    payload, error = _fetch_members_for_manager(user, query=query)
    if error:
        flash(error, 'danger')
        payload = {'teams': [], 'users': [], 'is_platform_admin': _is_platform_admin(user)}

    return render_template(
        'members.html',
        current_user=user,
        teams=payload.get('teams', []),
        users=payload.get('users', []),
        is_platform_admin=payload.get('is_platform_admin', False),
        query=query,
        role_labels=_role_labels(),
        can_edit_members=_can_edit_members(user),
    )


@bp.route('/members/<int:target_user_id>', methods=['GET', 'POST'])
@login_required
def member_detail(target_user_id):
    user = current_user()
    if not _can_view_members(user):
        flash('Kein Zugriff auf die Mitgliederverwaltung.', 'danger')
        return redirect(url_for('main.index'))

    can_edit_member = _can_edit_members(user)

    local_target_user = db.session.query(User).filter_by(auth_user_id=target_user_id).first()
    if not local_target_user:
        flash('Mitglied nicht gefunden.', 'danger')
        return redirect(url_for('main.members'))
    position_options = _fetch_position_options()
    position_labels = {item['key']: item['label'] for item in position_options}

    if request.method == 'POST':
        if not can_edit_member:
            flash('Du hast nur Lesezugriff auf Mitgliederdaten.', 'danger')
            return redirect(url_for('main.member_detail', target_user_id=target_user_id))

        selected_memberships = []
        for key in request.form:
            match = re.match(r'^team_(\d+)_role_(.+)$', key)
            if not match:
                continue
            try:
                team_id = int(match.group(1))
            except ValueError:
                continue
            role_key = match.group(2)
            selected_memberships.append({'team_id': team_id, 'member_role': role_key})

        first_name = (request.form.get('first_name') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower() or None
        phone = (request.form.get('phone') or '').strip() or None
        birth_date = _parse_iso_date(request.form.get('birth_date'))
        address_line1 = (request.form.get('address_line1') or '').strip() or None
        address_line2 = (request.form.get('address_line2') or '').strip() or None
        postal_code = (request.form.get('postal_code') or '').strip() or None
        city = (request.form.get('city') or '').strip() or None
        nationality = (request.form.get('nationality') or '').strip() or None
        shirt_size = (request.form.get('shirt_size') or '').strip() or None
        notes = (request.form.get('notes') or '').strip() or None
        profile_fields = {
            'first_name': first_name or None,
            'last_name': last_name or None,
            'email': email,
            'phone': phone,
            'birth_date': birth_date,
            'address_line1': address_line1,
            'address_line2': address_line2,
            'postal_code': postal_code,
            'city': city,
            'nationality': nationality,
            'shirt_size': shirt_size,
            'notes': notes,
            'license_number': (request.form.get('license_number') or '').strip() or None,
            'jersey_number': (request.form.get('jersey_number') or '').strip() or None,
            'position': (request.form.get('position') or '').strip() or None,
        }
        required_errors = _required_profile_errors({
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'phone': phone,
            'birth_date': birth_date,
            'address_line1': address_line1,
            'postal_code': postal_code,
            'city': city,
        })
        if required_errors:
            flash('Bitte alle Pflichtfelder ausfüllen: ' + ', '.join(required_errors) + '.', 'danger')
        else:
            ok, error = _update_user_profile_via_auth(user, local_target_user.auth_user_id, profile_fields)
            if ok:
                _update_member_profile_fields(local_target_user.id, profile_fields)
                ok, error = _update_member_memberships(user, target_user_id, selected_memberships)
                if ok:
                    db.session.commit()
                    flash('Mitgliedschaft aktualisiert.', 'success')
                    return redirect(url_for('main.member_detail', target_user_id=target_user_id))
                db.session.rollback()
            flash(error or 'Profildaten konnten nicht gespeichert werden.', 'danger')

    payload, error = _fetch_member_detail(user, target_user_id)
    target_profile = db.session.query(MemberProfile).filter_by(user_id=local_target_user.id).first()
    if error:
        flash(error, 'danger')
        return redirect(url_for('main.members'))

    return render_template(
        'member_form.html',
        current_user=user,
        user_payload=payload.get('user', {}),
        teams=payload.get('teams', []),
        is_platform_admin=payload.get('is_platform_admin', False),
        can_edit_member=can_edit_member,
        role_labels=_role_labels(),
        member_profile=target_profile,
        member_user=local_target_user,
        position_options=position_options,
        position_labels=position_labels,
        team_roles={
            int(team_id): (roles if isinstance(roles, list) else ([roles] if roles else []))
            for team_id, roles in (payload.get('user', {}).get('team_roles') or {}).items()
        },
    )


@bp.route('/antraege/approve/<int:target_user_id>', methods=['POST'])
@login_required
def approve_pending_user(target_user_id):
    user = current_user()
    if not _is_platform_admin(user) and not _has_members_manage_permission(user) and not _managed_team_codes(user):
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    ok, error = _approve_user_via_auth(user, target_user_id)
    if ok:
        flash('Benutzer erfolgreich freigegeben.', 'success')
    else:
        flash(error or 'Freigabe fehlgeschlagen.', 'danger')
    return redirect(url_for('main.antraege'))


@bp.route('/antraege/reject/<int:target_user_id>', methods=['POST'])
@login_required
def reject_pending_user(target_user_id):
    user = current_user()
    if not _is_platform_admin(user) and not _has_members_manage_permission(user) and not _managed_team_codes(user):
        flash('Kein Team-Manager-Zugriff vorhanden.', 'danger')
        return redirect(url_for('main.index'))

    reason = (request.form.get('reason') or '').strip()
    ok, error = _reject_user_via_auth(user, target_user_id, reason)
    if ok:
        flash('Antrag wurde abgelehnt.', 'success')
    else:
        flash(error or 'Ablehnung fehlgeschlagen.', 'danger')
    return redirect(url_for('main.antraege'))


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
    position_options = _fetch_position_options()
    position_labels = {item['key']: item['label'] for item in position_options}
    if request.method == 'POST':
        first_name = (request.form.get('first_name') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower() or None
        phone = (request.form.get('phone') or '').strip() or None
        birth_date = _parse_iso_date(request.form.get('birth_date'))
        address_line1 = (request.form.get('address_line1') or '').strip() or None
        address_line2 = (request.form.get('address_line2') or '').strip() or None
        postal_code = (request.form.get('postal_code') or '').strip() or None
        city = (request.form.get('city') or '').strip() or None
        nationality = (request.form.get('nationality') or '').strip() or None
        shirt_size = (request.form.get('shirt_size') or '').strip() or None
        notes = (request.form.get('notes') or '').strip() or None

        required_errors = _required_profile_errors({
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'phone': phone,
            'birth_date': birth_date,
            'address_line1': address_line1,
            'postal_code': postal_code,
            'city': city,
        })
        if required_errors:
            flash('Bitte alle Pflichtfelder ausfüllen: ' + ', '.join(required_errors) + '.', 'danger')
        else:
            already_complete = user.profile_complete
            profile_fields = {
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'phone': phone,
                'birth_date': birth_date,
                'address_line1': address_line1,
                'address_line2': address_line2,
                'postal_code': postal_code,
                'city': city,
                'nationality': nationality,
                'shirt_size': shirt_size,
                'notes': notes,
            }
            if not profile:
                profile = MemberProfile(user_id=user.id, first_name=first_name, last_name=last_name)
                db.session.add(profile)
            ok, error = _update_user_profile_via_auth(user, user.auth_user_id, profile_fields)
            if not ok:
                flash(error or 'Die Profildaten konnten nicht gespeichert werden.', 'danger')
            else:
                _update_member_profile_fields(user.id, profile_fields)
                db.session.commit()
                if already_complete:
                    flash('Profil erfolgreich aktualisiert.', 'success')
                    return redirect(url_for('main.index'))
                if _notify_auth_profile_complete(user.auth_user_id):
                    user.profile_complete = True
                    db.session.commit()
                    return redirect(url_for('main.submitted'))
                flash('Das Profil konnte nicht an TT-Auth übermittelt werden. Bitte erneut versuchen.', 'danger')

    return render_template(
        'profile.html',
        current_user=user,
        profile=profile,
        initial_first_name=profile.first_name if profile else (user.first_name or ''),
        initial_last_name=profile.last_name if profile else (user.last_name or ''),
        initial_email=profile.email if profile and profile.email is not None else (user.email or ''),
        position_labels=position_labels,
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
    memberships = normalize_memberships((user.claims_json or {}).get('memberships'))
    team_codes = {
        (membership.get('team_code') or '').strip().upper()
        for membership in memberships
        if membership.get('member_role') in {'team_manager', 'team_betreuer', 'head_coach'} and membership.get('team_code')
    }
    return sorted(code for code in team_codes if code)


def _viewer_team_codes(user):
    memberships = normalize_memberships((user.claims_json or {}).get('memberships'))
    team_codes = {
        (membership.get('team_code') or '').strip().upper()
        for membership in memberships
        if membership.get('member_role') in {'coach', 'team_manager', 'team_betreuer', 'head_coach'} and membership.get('team_code')
    }
    return sorted(code for code in team_codes if code)


def _is_platform_admin(user):
    if not user:
        return False
    claims = user.claims_json or {}
    return is_platform_admin(user.platform_role, normalize_permissions(claims.get('permissions')))


def _has_members_permission(user, permission_key):
    if not user:
        return False
    claims = user.claims_json or {}
    return has_role_permission(claims.get('role_permissions') or {}, permission_key, 'members')


def _has_members_manage_permission(user):
    return (
        _has_members_permission(user, 'create')
        or _has_members_permission(user, 'write')
        or _has_members_permission(user, 'update')
        or _has_members_permission(user, 'delete')
        or _has_members_permission(user, 'approve')
    )


def _can_view_members(user):
    return _is_platform_admin(user) or _has_members_permission(user, 'read') or bool(_viewer_team_codes(user))


def _can_edit_members(user):
    return _is_platform_admin(user) or _has_members_manage_permission(user) or bool(_managed_team_codes(user))


def _role_labels():
    return {
        'none': 'Ohne Rolle',
        'player': 'Spieler',
        'coach': 'Coach',
        'head_coach': 'Head Coach',
        'team_manager': 'Team-Manager',
        'team_betreuer': 'Team-Betreuer',
    }


def _fetch_position_options():
    infra_base = current_app.config.get('TT_INFRA_INTERNAL_URL', 'http://localhost:8084').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    fallback = [
        {'key': 'OL', 'label': 'OL'},
        {'key': 'DL', 'label': 'DL'},
        {'key': 'LB', 'label': 'LB'},
        {'key': 'RB', 'label': 'RB'},
        {'key': 'DB', 'label': 'DB'},
        {'key': 'TE', 'label': 'TE'},
        {'key': 'WR', 'label': 'WR'},
        {'key': 'QB', 'label': 'QB'},
    ]
    if not secret:
        return fallback

    try:
        response = requests.get(
            f'{infra_base}/api/master-data/positions',
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-infra positions fetch failed: %s %s', response.status_code, response.text)
            return fallback
        payload = response.json() or {}
        positions = payload.get('positions') or []
        cleaned = []
        for item in positions:
            key = (item.get('key') or '').strip()
            label = (item.get('label') or key).strip()
            if key:
                cleaned.append({'key': key, 'label': label or key})
        return cleaned or fallback
    except requests.RequestException as exc:
        current_app.logger.warning('tt-infra positions fetch failed: %s', exc)
        return fallback


def _required_profile_errors(values):
    labels = [
        ('first_name', 'Vorname'),
        ('last_name', 'Nachname'),
        ('email', 'E-Mail'),
        ('phone', 'Mobile Phone'),
        ('birth_date', 'Geburtsdatum'),
        ('address_line1', 'Adresse 1'),
        ('postal_code', 'Postleitzahl'),
        ('city', 'Ort'),
    ]
    missing = []
    for key, label in labels:
        value = values.get(key)
        if value is None or value == '':
            missing.append(label)
    return missing


def _auth_internal_request(method, path, *, params=None, json=None):
    auth_base = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return None, 'INTERNAL_API_SECRET ist nicht konfiguriert.'

    try:
        response = requests.request(
            method,
            f'{auth_base}{path}',
            params=params,
            json=json,
            headers={'X-TT-Internal-Secret': secret},
            timeout=4,
        )
        return response, None
    except requests.RequestException as exc:
        current_app.logger.warning('tt-auth member api failed: %s', exc)
        return None, 'Mitgliedsdaten konnten nicht geladen werden.'


def _fetch_members_for_manager(user, query=''):
    response, error = _auth_internal_request(
        'GET',
        '/api/team-manager/members',
        params={'approver_auth_user_id': user.auth_user_id, 'q': query or None},
    )
    if error:
        return None, error
    if response.status_code >= 400:
        current_app.logger.warning('tt-auth member list failed: %s %s', response.status_code, response.text)
        if response.status_code == 403:
            return None, 'Keine Berechtigung für diese Mitgliederliste.'
        return None, 'Mitgliedsdaten konnten nicht geladen werden.'
    return response.json() or {}, None


def _fetch_member_detail(user, target_user_id):
    response, error = _auth_internal_request(
        'GET',
        f'/api/team-manager/members/{target_user_id}',
        params={'approver_auth_user_id': user.auth_user_id},
    )
    if error:
        return None, error
    if response.status_code >= 400:
        current_app.logger.warning('tt-auth member detail failed: %s %s', response.status_code, response.text)
        if response.status_code == 403:
            return None, 'Keine Berechtigung für dieses Mitglied.'
        if response.status_code == 404:
            return None, 'Mitglied nicht gefunden.'
        return None, 'Mitgliedsdaten konnten nicht geladen werden.'
    return response.json() or {}, None


def _update_member_memberships(user, target_user_id, active_memberships):
    response, error = _auth_internal_request(
        'POST',
        f'/api/team-manager/members/{target_user_id}',
        json={
            'approver_auth_user_id': user.auth_user_id,
            'active_memberships': active_memberships,
        },
    )
    if error:
        return False, error
    if response.status_code >= 400:
        current_app.logger.warning('tt-auth member update failed: %s %s', response.status_code, response.text)
        if response.status_code == 403:
            return False, 'Keine Berechtigung für diese Änderung.'
        if response.status_code == 404:
            return False, 'Mitglied nicht gefunden.'
        return False, 'Mitgliedschaft konnte nicht gespeichert werden.'
    return True, None


def _update_user_profile_via_auth(user, target_user_id, profile_fields):
    if not target_user_id:
        return False, 'Mitglied nicht gefunden.'
    response, error = _auth_internal_request(
        'POST',
        f'/api/users/{target_user_id}/profile',
        json={
            'first_name': profile_fields.get('first_name'),
            'last_name': profile_fields.get('last_name'),
            'display_name': ' '.join(
                part for part in (profile_fields.get('first_name'), profile_fields.get('last_name')) if part
            ) or None,
            'email': profile_fields.get('email'),
        },
    )
    if error:
        return False, error
    if response.status_code >= 400:
        current_app.logger.warning('tt-auth profile update failed: %s %s', response.status_code, response.text)
        if response.status_code == 403:
            return False, 'Keine Berechtigung für diese Änderung.'
        if response.status_code == 404:
            return False, 'Mitglied nicht gefunden.'
        if response.status_code == 409:
            return False, 'Diese E-Mail-Adresse wird bereits verwendet.'
        return False, 'Profildaten konnten nicht gespeichert werden.'
    return True, None


def _update_member_profile_fields(target_user_id, profile_fields):
    profile = db.session.query(MemberProfile).filter_by(user_id=target_user_id).first()
    if not profile:
        if not any(value is not None for value in profile_fields.values()):
            return
        target_user = db.session.get(User, target_user_id)
        if not target_user:
            return
        first_name = (profile_fields.get('first_name') or target_user.first_name or '').strip()
        last_name = (profile_fields.get('last_name') or target_user.last_name or '').strip()
        if not first_name or not last_name:
            parts = [part for part in (target_user.display_name or target_user.username or '').split(' ') if part]
            if not first_name and parts:
                first_name = parts[0]
            if not last_name and len(parts) > 1:
                last_name = ' '.join(parts[1:])
        if not first_name or not last_name:
            current_app.logger.warning('Cannot create member profile for %s without names', target_user_id)
            return
        profile = MemberProfile(
            user_id=target_user_id,
            first_name=first_name,
            last_name=last_name,
        )
        db.session.add(profile)

    if 'first_name' in profile_fields:
        profile.first_name = profile_fields.get('first_name') or profile.first_name
    if 'last_name' in profile_fields:
        profile.last_name = profile_fields.get('last_name') or profile.last_name
    profile.email = profile_fields.get('email')
    profile.birth_date = profile_fields.get('birth_date')
    profile.license_number = profile_fields.get('license_number')
    profile.jersey_number = profile_fields.get('jersey_number')
    profile.position = profile_fields.get('position')
    profile.shirt_size = profile_fields.get('shirt_size')
    profile.phone = profile_fields.get('phone')
    profile.address_line1 = profile_fields.get('address_line1')
    profile.address_line2 = profile_fields.get('address_line2')
    profile.postal_code = profile_fields.get('postal_code')
    profile.city = profile_fields.get('city')
    profile.nationality = profile_fields.get('nationality')
    profile.notes = profile_fields.get('notes')

    target_user = db.session.get(User, target_user_id)
    if target_user:
        if profile_fields.get('first_name') is not None:
            target_user.first_name = profile_fields.get('first_name')
        if profile_fields.get('last_name') is not None:
            target_user.last_name = profile_fields.get('last_name')
        if profile_fields.get('email') is not None:
            target_user.email = profile_fields.get('email')
        if profile_fields.get('first_name') is not None or profile_fields.get('last_name') is not None:
            target_user.display_name = ' '.join(
                part for part in (target_user.first_name, target_user.last_name) if part
            ) or target_user.display_name

    db.session.commit()
    return True


def _parse_iso_date(value):
    text = (value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


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
