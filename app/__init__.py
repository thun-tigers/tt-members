from flask import Flask
from flask import session
from sqlalchemy import inspect, text

from .authz import has_role_permission, is_platform_admin, normalize_memberships, normalize_permissions
from .config import Config
from .extensions import db, limiter


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    limiter.init_app(app)

    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

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
            return {'pending_antraege_count': 0}

        from .models import User
        user = db.session.get(User, user_id)
        if not user:
            return {'pending_antraege_count': 0}

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
            return {'pending_antraege_count': 0}

        try:
            from .routes.main import _fetch_pending_users_for_manager
            pending_users, _ = _fetch_pending_users_for_manager(user)
            return {'pending_antraege_count': len(pending_users or [])}
        except Exception:
            return {'pending_antraege_count': 0}

    with app.app_context():
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
            db.session.commit()

    return app
