from functools import wraps
from datetime import date, datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, send_file, abort
from werkzeug.utils import secure_filename

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


ALLOWED_LICENSE_PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
LICENSE_PHOTO_STATUSES = {'none', 'pending', 'approved', 'rejected'}


def _license_photo_storage_dir():
    root = Path(current_app.config.get('UPLOAD_ROOT', str(Path('instance') / 'uploads')))
    directory = root / 'license-photos'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_license_photo_extension(filename):
    suffix = Path(filename or '').suffix.lower()
    return suffix if suffix in ALLOWED_LICENSE_PHOTO_EXTENSIONS else ''


def _remove_license_photo_file(filename):
    if not filename:
        return
    file_path = _license_photo_storage_dir() / filename
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass


def _store_license_photo_upload(profile, upload_file):
    if not upload_file or not upload_file.filename:
        return False, 'Bitte ein Lizenzfoto auswählen.'

    extension = _safe_license_photo_extension(upload_file.filename)
    if not extension:
        return False, 'Erlaubt sind nur JPG, PNG oder WEBP.'

    original_name = secure_filename(upload_file.filename)
    photo_filename = f'license_{profile.user_id}_{uuid4().hex}{extension}'
    destination = _license_photo_storage_dir() / photo_filename

    old_filename = profile.license_photo_filename
    upload_file.save(destination)
    if old_filename and old_filename != photo_filename:
        _remove_license_photo_file(old_filename)

    profile.license_photo_filename = photo_filename
    profile.license_photo_status = 'pending'
    profile.license_photo_review_reason = None
    profile.license_photo_uploaded_at = datetime.now(timezone.utc)
    profile.license_photo_reviewed_at = None
    profile.license_photo_reviewed_by_user_id = None
    return True, None


def _can_review_license_photo(user):
    if not user:
        return False
    if _is_platform_admin(user):
        return True
    memberships = normalize_memberships((user.claims_json or {}).get('memberships'))
    return any(
        membership.get('is_active', True) and membership.get('member_role') in {'head_coach', 'team_manager'}
        for membership in memberships
    )


def _license_photo_status_label(status):
    return {
        'none': 'Kein Foto',
        'pending': 'In Prüfung',
        'approved': 'Freigegeben',
        'rejected': 'Zurückgewiesen',
    }.get((status or 'none').lower(), 'Unbekannt')


def _personal_message_items(user):
    items = []
    profile = db.session.query(MemberProfile).filter_by(user_id=user.id).first() if user else None

    if profile and profile.license_photo_status == 'pending':
        items.append({
            'kind': 'license_photo',
            'severity': 'warning',
            'title': 'Dein Lizenzfoto ist in Prüfung',
            'message': 'Ein Berechtigter prüft dein Foto gerade.',
            'action_label': 'Profil öffnen',
            'action_url': url_for('main.profile'),
        })
    elif profile and profile.license_photo_status == 'rejected':
        items.append({
            'kind': 'license_photo',
            'severity': 'danger',
            'title': 'Dein Lizenzfoto wurde zurückgewiesen',
            'message': profile.license_photo_review_reason or 'Bitte lade ein neues Foto hoch.',
            'action_label': 'Foto ersetzen',
            'action_url': url_for('main.profile'),
        })

    pending_memberships = normalize_memberships((user.claims_json or {}).get('pending_memberships')) if user else []
    for membership in pending_memberships:
        items.append({
            'kind': 'membership',
            'severity': 'info',
            'title': 'Deine Team-Anfrage ist offen',
            'message': f"{membership.get('team_name') or membership.get('team_code')}: {membership.get('member_role')}",
            'action_label': None,
            'action_url': None,
        })

    return items


def _manager_message_items(user):
    if not _can_review_license_photo(user):
        return []

    items = []
    pending_users, _ = _fetch_pending_users_for_manager(user)
    pending_license_photos, _ = _fetch_pending_license_photos_for_manager(user)

    for pending_user in pending_users or []:
        items.append({
            'kind': 'team_request',
            'severity': 'info',
            'title': 'Neue Team-Anfrage',
            'message': pending_user.get('display_name') or pending_user.get('username') or 'Unbekannt',
            'action_label': 'Messages öffnen',
            'action_url': url_for('main.messages'),
        })

    for photo in pending_license_photos or []:
        items.append({
            'kind': 'license_review',
            'severity': 'warning',
            'title': 'Lizenzfoto wartet auf Prüfung',
            'message': photo.get('display_name') or photo.get('username') or 'Unbekannt',
            'action_label': 'Foto prüfen',
            'action_url': url_for('main.messages'),
        })

    return items


def _message_items_for_user(user):
    if not user:
        return []
    items = _personal_message_items(user)
    items.extend(_manager_message_items(user))
    return items


def _member_display_name(user, profile=None):
    if profile:
        full_name = ' '.join(part for part in (profile.first_name, profile.last_name) if part)
        if full_name:
            return full_name
    if user:
        return user.display_name or user.username
    return 'Unbekannt'


def _fetch_pending_license_photos_for_manager(user):
    if not _can_review_license_photo(user):
        return [], None

    query = (
        db.session.query(MemberProfile, User)
        .join(User, User.id == MemberProfile.user_id)
        .filter(MemberProfile.license_photo_status == 'pending')
        .order_by(MemberProfile.license_photo_uploaded_at.asc().nullslast(), User.display_name.asc(), User.username.asc())
    )
    items = []
    managed_team_codes = set(_managed_team_codes(user)) if not _is_platform_admin(user) else set()

    for profile, member_user in query.all():
        if managed_team_codes:
            memberships = normalize_memberships((member_user.claims_json or {}).get('memberships'))
            visible = any(
                (membership.get('team_code') or '').strip().upper() in managed_team_codes
                for membership in memberships
                if membership.get('team_code')
            )
            if not visible:
                continue

        items.append({
            'member_user_id': member_user.id,
            'auth_user_id': member_user.auth_user_id,
            'username': member_user.username,
            'display_name': _member_display_name(member_user, profile),
            'email': profile.email or member_user.email,
            'uploaded_at': profile.license_photo_uploaded_at,
            'review_reason': profile.license_photo_review_reason,
            'status': profile.license_photo_status,
            'license_photo_filename': profile.license_photo_filename,
        })

    return items, None


def _member_profile_for_auth_user_id(target_user_id):
    member_user = db.session.query(User).filter_by(auth_user_id=target_user_id).first()
    if not member_user:
        return None, None
    profile = db.session.query(MemberProfile).filter_by(user_id=member_user.id).first()
    return member_user, profile


@bp.route('/members/<int:member_user_id>/license-photo')
@login_required
def license_photo_file(member_user_id):
    user = current_user()
    if not user:
        abort(403)

    member_user = db.session.get(User, member_user_id)
    if not member_user:
        abort(404)

    if user.id != member_user_id and not (_can_review_license_photo(user) or _can_view_members(user)):
        abort(403)

    profile = db.session.query(MemberProfile).filter_by(user_id=member_user_id).first()
    if not profile or not profile.license_photo_filename:
        abort(404)

    photo_path = (_license_photo_storage_dir() / profile.license_photo_filename).resolve()
    if not photo_path.exists() or not photo_path.is_file():
        current_app.logger.warning('license photo missing on disk: %s', photo_path)
        abort(404)

    return send_file(photo_path)


@bp.route('/health')
def health():
    return {'status': 'ok'}, 200


@bp.route('/')
@login_required
def index():
    user = current_user()
    if _can_view_members(user):
        return redirect(url_for('main.members'))
    if not user.profile_complete or not user.profile:
        return redirect(url_for('main.profile'))
    return render_template('dashboard.html', current_user=user)


@bp.route('/messages')
@login_required
def messages():
    user = current_user()
    is_platform_admin = _is_platform_admin(user)
    managed_team_codes = _managed_team_codes(user)
    pending_users = []
    pending_license_photos = []
    if is_platform_admin or _has_members_manage_permission(user) or managed_team_codes:
        pending_users, fetch_error = _fetch_pending_users_for_manager(user)
        pending_license_photos, photo_error = _fetch_pending_license_photos_for_manager(user)
        if fetch_error:
            flash(fetch_error, 'danger')
        if photo_error:
            flash(photo_error, 'danger')

    return render_template(
        'team_manager.html',
        current_user=user,
        is_platform_admin=is_platform_admin,
        managed_team_codes=managed_team_codes,
        pending_users=pending_users,
        pending_license_photos=pending_license_photos,
        message_items=_message_items_for_user(user),
    )


@bp.route('/antraege')
@login_required
def antraege():
    return redirect(url_for('main.messages'))


@bp.route('/members')
@login_required
def members():
    user = current_user()
    if not _can_view_members(user):
        flash('Kein Zugriff auf die Mitgliederverwaltung.', 'danger')
        return redirect(url_for('main.index'))

    payload, error = _fetch_members_for_manager(user)
    if error:
        flash(error, 'danger')
        payload = {'teams': [], 'users': [], 'is_platform_admin': _is_platform_admin(user)}

    # Enrich users with position from local member_profile
    raw_users = payload.get('users', [])
    if raw_users:
        auth_ids = [u['id'] for u in raw_users if u.get('id')]
        local_users = db.session.query(User).filter(User.auth_user_id.in_(auth_ids)).all()
        profiles_by_auth_id = {}
        if local_users:
            local_ids = [u.id for u in local_users]
            profiles = db.session.query(MemberProfile).filter(MemberProfile.user_id.in_(local_ids)).all()
            profiles_by_local_id = {p.user_id: p for p in profiles}
            for lu in local_users:
                p = profiles_by_local_id.get(lu.id)
                profiles_by_auth_id[lu.auth_user_id] = {'position': p.position if p else None}
        for u in raw_users:
            u['profile'] = profiles_by_auth_id.get(u.get('id'), {'position': None})

    visible_teams = sorted({
        (m.get('team', {}).get('code') or '').strip().upper()
        for u in raw_users
        for m in (u.get('active_memberships') or [])
        if (m.get('team', {}).get('code') or '').strip()
    })

    return render_template(
        'members.html',
        current_user=user,
        teams=payload.get('teams', []),
        users=raw_users,
        is_platform_admin=payload.get('is_platform_admin', False),
        role_labels=_role_labels(),
        can_edit_members=_can_edit_members(user),
        visible_teams=visible_teams,
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
        license_photo = request.files.get('license_photo')
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
                profile = db.session.query(MemberProfile).filter_by(user_id=local_target_user.id).first()
                if license_photo and license_photo.filename and profile:
                    ok_photo, photo_error = _store_license_photo_upload(profile, license_photo)
                    if not ok_photo:
                        db.session.rollback()
                        flash(photo_error or 'Lizenzfoto konnte nicht gespeichert werden.', 'danger')
                        return redirect(url_for('main.member_detail', target_user_id=target_user_id))
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
        can_review_license_photo=_can_review_license_photo(user),
        license_photo_status_label=_license_photo_status_label,
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
    profile = db.session.query(MemberProfile).filter_by(user_id=user.id).first()
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
        license_photo = request.files.get('license_photo')

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
                profile = db.session.query(MemberProfile).filter_by(user_id=user.id).first()
                if license_photo and license_photo.filename and profile:
                    ok_photo, photo_error = _store_license_photo_upload(profile, license_photo)
                    if not ok_photo:
                        db.session.rollback()
                        flash(photo_error or 'Lizenzfoto konnte nicht gespeichert werden.', 'danger')
                        return redirect(url_for('main.profile'))
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
        can_review_license_photo=_can_review_license_photo(user),
        license_photo_status_label=_license_photo_status_label,
    )


@bp.route('/members/<int:target_user_id>/license-photo/approve', methods=['POST'])
@login_required
def approve_license_photo(target_user_id):
    user = current_user()
    if not _can_review_license_photo(user):
        flash('Keine Berechtigung für diese Freigabe.', 'danger')
        return redirect(url_for('main.member_detail', target_user_id=target_user_id))

    member_user, profile = _member_profile_for_auth_user_id(target_user_id)
    if not member_user or not profile or profile.license_photo_status == 'none':
        flash('Kein Lizenzfoto vorhanden.', 'warning')
        return redirect(url_for('main.member_detail', target_user_id=target_user_id))

    profile.license_photo_status = 'approved'
    profile.license_photo_review_reason = None
    profile.license_photo_reviewed_at = datetime.now(timezone.utc)
    profile.license_photo_reviewed_by_user_id = user.id
    db.session.commit()
    flash('Lizenzfoto freigegeben.', 'success')
    return redirect(url_for('main.member_detail', target_user_id=target_user_id))


@bp.route('/members/<int:target_user_id>/license-photo/reject', methods=['POST'])
@login_required
def reject_license_photo(target_user_id):
    user = current_user()
    if not _can_review_license_photo(user):
        flash('Keine Berechtigung für diese Ablehnung.', 'danger')
        return redirect(url_for('main.member_detail', target_user_id=target_user_id))

    reason = (request.form.get('reason') or '').strip() or None
    member_user, profile = _member_profile_for_auth_user_id(target_user_id)
    if not member_user or not profile or profile.license_photo_status == 'none':
        flash('Kein Lizenzfoto vorhanden.', 'warning')
        return redirect(url_for('main.member_detail', target_user_id=target_user_id))

    profile.license_photo_status = 'rejected'
    profile.license_photo_review_reason = reason
    profile.license_photo_reviewed_at = datetime.now(timezone.utc)
    profile.license_photo_reviewed_by_user_id = user.id
    db.session.commit()
    flash('Lizenzfoto zurückgewiesen.', 'success')
    return redirect(url_for('main.member_detail', target_user_id=target_user_id))


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


def _fetch_members_for_manager(user):
    response, error = _auth_internal_request(
        'GET',
        '/api/team-manager/members',
        params={'approver_auth_user_id': user.auth_user_id},
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
