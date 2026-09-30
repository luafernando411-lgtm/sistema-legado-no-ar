from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from pathlib import Path
import os
import secrets

db = SQLAlchemy()
jwt = JWTManager()

def create_app(initialize_db=True):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    base = Path(app.root_path).parent
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_urlsafe(48),
        JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY") or secrets.token_urlsafe(48),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", f"sqlite:///{base/'censo.db'}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        CENSO_READS_REQUIRE_AUTH=os.getenv("CENSO_READS_REQUIRE_AUTH","false").lower()=="true",
    )
    CORS(app)
    db.init_app(app)
    jwt.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    if initialize_db:
        with app.app_context():
            from . import models  # noqa
            db.create_all()
            from .seed import ensure_admin_and_seed
            ensure_admin_and_seed(
                os.getenv("CENSO_LUIZ_PASSWORD"),
                os.getenv("CENSO_JOSY_PASSWORD"),
            )

    return app
