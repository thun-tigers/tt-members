from flask import Flask
from flask import session
from pathlib import Path
from sqlalchemy import inspect, text
import logging

from werkzeug.middleware.proxy_fix import ProxyFix
from .authz import has_role_permission, is_platform_admin, normalize_memberships, normalize_permissions
from .config import Config
from .db_bootstrap import schema_setup_lock
from .extensions import db, limiter


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Logging
    log_level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    formatter = logging.Formatter('[%(asctime)s +0000] [%(process)d] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Flask session config
    app.config.setdefault('SESSION_COOKIE_SECURE', True)
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')

    db.init_app(app)
    limiter.init_app(app)

    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    # Zentrales UI-Layout aus tt-common
    from tt_common import register_shared_ui
    register_shared_ui(
        app,
        brand_label='Members',
        brand_icon='bi-people-fill',
        home_endpoint='main.index',
        profile_endpoint='main.profile',
        logout_endpoint='auth.logout',
    )

    @app.context_processor
    def inject_platform_links():
        auth_base_url = app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
        return {
            'auth_base_url': auth_base_url,
            'auth_dashboard_url': f'{auth_base_url}/',
        }

    @app.context_processor
    def inject_member_access_flags():
        user_id = session.get('user_id')
        if not user_id:
            return {
                'has_member_admin_access': False,
                'has_member_edit_access': False,
                'has_team_manager_access': False,
            }

        from .models import User
        user = db.session.get(User, user_id)
        if not user:
            return {
                'has_member_admin_access': False,
                'has_member_edit_access': False,
                'has_team_manager_access': False,
            }

        claims = user.claims_json or {}
        permissions = normalize_permissions(claims.get('permissions'))
        role_permissions = claims.get('role_permissions') or {}
        memberships = normalize_memberships(claims.get('memberships'))
        is_admin = is_platform_admin(user.platform_role, permissions)

        active_roles = {
            membership.get('member_role')
            for membership in memberships
            if membership.get('member_role')
        }

        has_member_admin_access = (
            is_admin
            or has_role_permission(role_permissions, 'read', 'members')
            or bool(active_roles.intersection({'coach', 'head_coach', 'team_manager', 'team_betreuer'}))
        )
        has_member_edit_access = (
            is_admin
            or has_role_permission(role_permissions, 'create', 'members')
            or has_role_permission(role_permissions, 'write', 'members')
            or has_role_permission(role_permissions, 'approve', 'members')
            or bool(active_roles.intersection({'head_coach', 'team_manager', 'team_betreuer'}))
        )
        has_team_manager_access = (
            is_admin
            or has_role_permission(role_permissions, 'approve', 'members')
            or bool(active_roles.intersection({'head_coach', 'team_manager', 'team_betreuer'}))
        )

        return {
            'has_member_admin_access': has_member_admin_access,
            'has_member_edit_access': has_member_edit_access,
            'has_team_manager_access': has_team_manager_access,
        }

    @app.context_processor
    def inject_pending_antraege_count():
        user_id = session.get('user_id')
        if not user_id:
            return {'pending_antraege_count': 0, 'pending_messages_count': 0, 'message_items': []}

        from .models import User
        user = db.session.get(User, user_id)
        if not user:
            return {'pending_antraege_count': 0, 'pending_messages_count': 0, 'message_items': []}

        claims = user.claims_json or {}
        permissions = normalize_permissions(claims.get('permissions'))
        role_permissions = claims.get('role_permissions') or {}
        managed_team = is_platform_admin(user.platform_role, permissions)
        if not managed_team:
            managed_team = (
                has_role_permission(role_permissions, 'create', 'members')
                or has_role_permission(role_permissions, 'write', 'members')
                or has_role_permission(role_permissions, 'approve', 'members')
            )
        if not managed_team:
            memberships = normalize_memberships(claims.get('memberships'))
            managed_team = any(
                membership.get('member_role') in {'team_manager', 'team_betreuer', 'head_coach'}
                for membership in memberships
            )
        if not managed_team:
            return {'pending_antraege_count': 0, 'pending_messages_count': 0, 'message_items': []}

        try:
            from .routes.main import _message_items_for_user
            message_items = _message_items_for_user(user)
            return {
                'pending_antraege_count': len(message_items or []),
                'pending_messages_count': len(message_items or []),
                'message_items': message_items or [],
            }
        except Exception:
            return {'pending_antraege_count': 0, 'pending_messages_count': 0, 'message_items': []}

    with app.app_context():
        with schema_setup_lock(db.engine):
            db.create_all()
            inspector = inspect(db.engine)
            if 'user' in inspector.get_table_names():
                columns = {column['name'] for column in inspector.get_columns('user')}
                if 'first_name' not in columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN first_name VARCHAR(80)'))
                if 'last_name' not in columns:
                    db.session.execute(text('ALTER TABLE user ADD COLUMN last_name VARCHAR(80)'))
            if 'member_profile' in inspector.get_table_names():
                columns = {column['name'] for column in inspector.get_columns('member_profile')}
                if 'email' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN email VARCHAR(255)'))
                if 'birth_date' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN birth_date DATE'))
                if 'address_line1' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN address_line1 VARCHAR(120)'))
                if 'address_line2' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN address_line2 VARCHAR(120)'))
                if 'postal_code' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN postal_code VARCHAR(20)'))
                if 'city' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN city VARCHAR(120)'))
                if 'nationality' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN nationality VARCHAR(80)'))
                if 'license_number' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_number VARCHAR(80)'))
                if 'jersey_number' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN jersey_number VARCHAR(40)'))
                if 'position' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN position VARCHAR(80)'))
                if 'shirt_size' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN shirt_size VARCHAR(40)'))
                if 'license_photo_filename' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_photo_filename VARCHAR(255)'))
                if 'license_photo_status' not in columns:
                    db.session.execute(text("ALTER TABLE member_profile ADD COLUMN license_photo_status VARCHAR(20) NOT NULL DEFAULT 'none'"))
                if 'license_photo_review_reason' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_photo_review_reason TEXT'))
                if 'license_photo_uploaded_at' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_photo_uploaded_at TIMESTAMP'))
                if 'license_photo_reviewed_at' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_photo_reviewed_at TIMESTAMP'))
                if 'license_photo_reviewed_by_user_id' not in columns:
                    db.session.execute(text('ALTER TABLE member_profile ADD COLUMN license_photo_reviewed_by_user_id INTEGER'))
                db.session.commit()

        upload_root = Path(app.config.get('UPLOAD_ROOT', str(Path('instance') / 'uploads')))
        (upload_root / 'license-photos').mkdir(parents=True, exist_ok=True)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    return app
