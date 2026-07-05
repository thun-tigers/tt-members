from datetime import datetime, timezone

from .extensions import db
from .authz import normalize_auth_payload


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    auth_user_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    display_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    platform_role = db.Column(db.String(32), nullable=False, default='user')
    service_role = db.Column(db.String(32), nullable=False, default='user')
    profile_complete = db.Column(db.Boolean, nullable=False, default=False)
    claims_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def sync_from_sso_claims(self, payload):
        auth = normalize_auth_payload(payload)
        claims = auth["claims"]

        self.auth_user_id = int(claims["sub"])
        self.username = (claims.get("username") or self.username).strip()
        self.first_name = claims.get("first_name")
        self.last_name = claims.get("last_name")
        self.display_name = claims.get("display_name") or self.username
        self.email = claims.get("email")
        self.platform_role = auth["platform_role"]
        self.service_role = auth["service_role"]
        self.profile_complete = bool(claims.get("profile_complete"))
        self.claims_json = claims


class MemberProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    birth_date = db.Column(db.Date, nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    address_line1 = db.Column(db.String(120), nullable=True)
    address_line2 = db.Column(db.String(120), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    nationality = db.Column(db.String(80), nullable=True)
    license_number = db.Column(db.String(80), nullable=True)
    jersey_number = db.Column(db.String(40), nullable=True)
    position = db.Column(db.String(80), nullable=True)
    shirt_size = db.Column(db.String(40), nullable=True)
    license_photo_filename = db.Column(db.String(255), nullable=True)
    license_photo_status = db.Column(db.String(20), nullable=False, default='none')
    license_photo_review_reason = db.Column(db.Text, nullable=True)
    license_photo_uploaded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    license_photo_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    license_photo_reviewed_by_user_id = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    user = db.relationship('User', backref=db.backref('profile', uselist=False, cascade='all, delete-orphan'))
