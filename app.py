import os
import secrets
from pathlib import Path

from flask import Flask, request
from flask_jwt_extended import JWTManager
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

__path__ = [str(Path(__file__).resolve().parent)]
__spec__.submodule_search_locations = __path__
__package__ = __name__
load_dotenv()

db = SQLAlchemy()
jwt = JWTManager()


def database_url():
    value = os.getenv("DATABASE_URL")
    if not value:
        return "sqlite:///" + str(Path(__file__).resolve().parent / "censo.db")
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def create_app(initialize_db=True):
    base = Path(__file__).resolve().parent
    instance_path = Path(os.getenv("APP_INSTANCE_PATH", base / "instance")).resolve()
    app = Flask(__name__, static_folder=None, instance_path=str(instance_path), instance_relative_config=True)

    def configured_secret(name):
        value=os.getenv(name)
        if value:
            if len(value)<32:
                raise RuntimeError(f"{name} precisa ter pelo menos 32 caracteres")
            return value
        instance_path.mkdir(parents=True,exist_ok=True)
        secret_path=instance_path/f".{name.lower()}"
        if not secret_path.exists():
            temporary=instance_path/f".{name.lower()}.{secrets.token_hex(8)}.tmp"
            try:
                with temporary.open("x") as secret_file:
                    secret_file.write(secrets.token_urlsafe(64))
                os.chmod(temporary,0o600)
                try:
                    os.link(temporary,secret_path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        os.chmod(secret_path,0o600)
        return secret_path.read_text().strip()

    app.config.from_mapping(
        SECRET_KEY=configured_secret("SECRET_KEY"),
        JWT_SECRET_KEY=configured_secret("JWT_SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 280},
        MAX_FILE_SIZE=6 * 1024**3,
        MAX_CONTENT_LENGTH=6 * 1024**3 + 1024 * 1024,
        UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", str(base / "uploads")),
        CENSO_READS_REQUIRE_AUTH=True,
        JWT_TOKEN_LOCATION=["cookies"],
        JWT_COOKIE_CSRF_PROTECT=True,
        JWT_COOKIE_SECURE=os.getenv(
            "JWT_COOKIE_SECURE",
            "true" if os.getenv("APP_ENV") == "production" or os.getenv("RENDER") == "true" else "false",
        ).lower() == "true",
        JWT_COOKIE_SAMESITE="Lax",
        JWT_ACCESS_TOKEN_EXPIRES=1800,
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", app.config["SQLALCHEMY_DATABASE_URI"])
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", app.config["UPLOAD_FOLDER"])

    db.init_app(app)
    jwt.init_app(app)

    from .routes import bp

    app.register_blueprint(bp)

    @app.after_request
    def secure_response_headers(response):
        response.headers.setdefault("X-Content-Type-Options","nosniff")
        response.headers.setdefault("X-Frame-Options","DENY")
        response.headers.setdefault("Referrer-Policy","no-referrer")
        response.headers.setdefault("Permissions-Policy","camera=(), microphone=(), geolocation=()")
        if request.path == "/" or request.path.startswith("/api/"):
            response.headers["Cache-Control"]="no-store, private"
        return response

    def initialize_database():
        with app.app_context():
            from . import models
            from .seed import ensure_admin_and_seed

            db.create_all()
            from sqlalchemy import inspect, text
            if "status" not in {column["name"] for column in inspect(db.engine).get_columns("pendencia_importada")}:
                db.session.execute(text("ALTER TABLE pendencia_importada ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Pendente'"))
                db.session.commit()
            Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
            ensure_admin_and_seed(
                os.getenv("CENSO_LUIZ_PASSWORD"),
                os.getenv("CENSO_JOSY_PASSWORD"),
            )

    @app.cli.command("init-db")
    def initialize_database_command():
        initialize_database()
        print("Banco e usuários autorizados inicializados.")

    if initialize_db:
        initialize_database()

    return app


app = create_app(initialize_db=False)
