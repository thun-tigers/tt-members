from flask import Flask
from sqlalchemy import inspect, text

from .config import Config
from .extensions import db, limiter


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    limiter.init_app(app)

    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        if 'user' in inspector.get_table_names():
            columns = {column['name'] for column in inspector.get_columns('user')}
            if 'first_name' not in columns:
                db.session.execute(text('ALTER TABLE user ADD COLUMN first_name VARCHAR(80)'))
            if 'last_name' not in columns:
                db.session.execute(text('ALTER TABLE user ADD COLUMN last_name VARCHAR(80)'))
            db.session.commit()

    return app
