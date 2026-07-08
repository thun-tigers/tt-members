import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'tt-members-dev-secret')
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('SQLALCHEMY_DATABASE_URI')
        or os.environ.get('DATABASE_URL')
        or 'postgresql+psycopg://tt_members:tt_members_password@tt-postgres-members:5432/tt_members'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    AUTH_BASE_URL = os.environ.get('AUTH_BASE_URL', 'http://localhost:8085')
    TT_INFRA_INTERNAL_URL = os.environ.get('TT_INFRA_INTERNAL_URL')
    SSO_SHARED_SECRET = os.environ.get('SSO_SHARED_SECRET') or SECRET_KEY
    SSO_EXPECTED_AUDIENCE = os.environ.get('SSO_EXPECTED_AUDIENCE', 'tt-members')
    SSO_REPLAY_STORAGE_URI = os.environ.get('SSO_REPLAY_STORAGE_URI', '')
    SSO_REPLAY_TTL_SECONDS = int(os.environ.get('SSO_REPLAY_TTL_SECONDS', 300))
    INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET', 'tt-internal-dev-secret-change-me')
    TT_AUTH_INTERNAL_URL = os.environ.get('TT_AUTH_INTERNAL_URL')
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    UPLOAD_ROOT = os.environ.get('UPLOAD_ROOT', str(Path('instance') / 'uploads'))
