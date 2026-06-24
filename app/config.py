import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'tt-members-dev-secret')
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('SQLALCHEMY_DATABASE_URI')
        or os.environ.get('DATABASE_URL')
        or 'sqlite:///members.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTH_BASE_URL = os.environ.get('AUTH_BASE_URL', 'http://localhost:8085')
    TT_INFRA_INTERNAL_URL = os.environ.get('TT_INFRA_INTERNAL_URL', 'http://localhost:8084')
    SSO_SHARED_SECRET = os.environ.get('SSO_SHARED_SECRET') or SECRET_KEY
    SSO_EXPECTED_AUDIENCE = os.environ.get('SSO_EXPECTED_AUDIENCE', 'tt-members')
    INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET', 'tt-internal-dev-secret-change-me')
    TT_AUTH_INTERNAL_URL = os.environ.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000')
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
