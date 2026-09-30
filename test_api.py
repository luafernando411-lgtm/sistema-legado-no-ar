import os
import secrets
from io import BytesIO
import pytest
os.environ["DATABASE_URL"]="sqlite:///:memory:"
from app import create_app
from app import db
from app.models import PendenciaImportada, Recenseado
from app.seed import ensure_admin_and_seed
from app import routes as api_routes
from app.utils import normalize_cpf

TEST_PASSWORD=os.getenv("TEST_PASSWORD") or secrets.token_urlsafe(24)

@pytest.fixture(autouse=True)
def isolated_security_config(monkeypatch,tmp_path):
    monkeypatch.setenv("DATABASE_URL","sqlite:///:memory:")
    monkeypatch.setenv("APP_INSTANCE_PATH",str(tmp_path/"instance"))
    monkeypatch.setenv("SECRET_KEY","test-only-flask-signing-key-32-bytes-minimum")
    monkeypatch.setenv("JWT_SECRET_KEY","test-only-jwt-signing-key-32-bytes-minimum")
    monkeypatch.setenv("CENSO_LUIZ_PASSWORD",TEST_PASSWORD)
    monkeypatch.setenv("CENSO_JOSY_PASSWORD",TEST_PASSWORD)

def test_health():
    app=create_app()
    c=app.test_client()
    assert c.get("/health").status_code==200

def test_api_status_endpoint_is_public_and_no_store():
    response=create_app().test_client().get("/api/status")
    assert response.status_code==200
    assert response.get_json()=={"mensagem":"API Python rodando!","status":"ok"}
    assert "no-store" in response.headers["Cache-Control"]

def test_login():
    app=create_app()
    c=app.test_client()
    r=c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD})
    assert r.status_code==200
    assert r.headers.getlist("Set-Cookie")
    assert r.get_json()=={"ok":True}

def test_login_accepts_configured_email_alias(monkeypatch):
    monkeypatch.setenv("CENSO_JOSY_EMAIL","josy@example.com")
    app=create_app()
    c=app.test_client()
    response=c.post("/api/auth/login",json={"username":"josy@example.com","password":TEST_PASSWORD})
    assert response.status_code==200

def test_only_configured_users_can_login():
    app=create_app()
    c=app.test_client()
    assert c.post("/api/auth/login",json={"username":"admin","password":secrets.token_urlsafe(12)}).status_code==401
    assert c.post("/api/auth/login",json={"username":"luizarrow3","password":TEST_PASSWORD}).status_code==200

def test_repeated_failed_logins_are_temporarily_blocked():
    app=create_app()
    c=app.test_client()
    for _ in range(5):
        c.post("/api/auth/login",json={"username":"josy","password":"incorreta"})
    assert c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD}).status_code==429

def test_dashboard_requires_login():
    app=create_app()
    c=app.test_client()
    assert c.get("/api/dashboard").status_code==401
    assert c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD}).status_code==200
    assert c.get("/api/dashboard").status_code==200

def test_refresh_revokes_session_and_requires_login_again():
    app=create_app()
    c=app.test_client()
    assert c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD}).status_code==200
    assert c.get("/api/dashboard").status_code==200
    refreshed=c.get("/")
    assert refreshed.status_code==200
    assert "no-store" in refreshed.headers["Cache-Control"]
    assert c.get("/api/dashboard").status_code==401

def test_private_api_responses_never_cache():
    app=create_app()
    c=app.test_client()
    c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD})
    response=c.get("/api/dashboard")
    assert response.status_code==200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["X-Frame-Options"]=="DENY"
    assert response.headers["X-Content-Type-Options"]=="nosniff"

def test_file_area_supports_folder_upload_and_download(tmp_path):
    app=create_app()
    app.config["UPLOAD_FOLDER"]=str(tmp_path)
    c=app.test_client()
    assert c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD}).status_code==200
    csrf=c.get_cookie("csrf_access_token").value
    headers={"X-CSRF-TOKEN":csrf}

    folder=c.post("/api/arquivos/pastas",json={"nome":"Documentos"},headers=headers)
    assert folder.status_code==201
    folder_id=folder.get_json()["id"]
    uploaded=c.post(
        "/api/arquivos/upload",
        data={"pasta_id":str(folder_id),"arquivo":(BytesIO(b"conteudo de teste"),"teste.txt")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert uploaded.status_code==201
    file_id=uploaded.get_json()["id"]
    listing=c.get(f"/api/arquivos?pasta_id={folder_id}")
    assert listing.get_json()["arquivos"][0]["nome"]=="teste.txt"
    assert c.get(f"/api/arquivos/{file_id}/download").data==b"conteudo de teste"

def test_file_upload_enforces_configured_limit(tmp_path):
    app=create_app()
    app.config["UPLOAD_FOLDER"]=str(tmp_path)
    app.config["MAX_FILE_SIZE"]=4
    c=app.test_client()
    c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD})
    headers={"X-CSRF-TOKEN":c.get_cookie("csrf_access_token").value}
    response=c.post(
        "/api/arquivos/upload",
        data={"arquivo":(BytesIO(b"maior"),"teste.bin")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code==413
    assert list(tmp_path.iterdir())==[]

def test_workbooks_import_records_and_pending_rows_idempotently():
    app=create_app()
    with app.app_context():
        initial_records=Recenseado.query.count()
        initial_pending=PendenciaImportada.query.count()
        assert initial_records==126
        assert initial_pending==20
        assert Recenseado.query.filter(db.func.length(Recenseado.cpf)!=11).count()==0
        assert PendenciaImportada.query.filter(PendenciaImportada.recenseado_id.isnot(None)).count()==3
        assert PendenciaImportada.query.filter(PendenciaImportada.recenseado_id.is_(None)).count()==17
        ensure_admin_and_seed(TEST_PASSWORD,TEST_PASSWORD)
        assert Recenseado.query.count()==initial_records
        assert PendenciaImportada.query.count()==initial_pending

def test_ibge_search_joins_local_functional_counts(monkeypatch):
    municipality={
        "id":2510402,
        "nome":"Olho d'Água",
        "microrregiao":{"mesorregiao":{"UF":{"sigla":"PB","regiao":{"nome":"Nordeste"}}}},
    }
    monkeypatch.setattr(api_routes,"_ibge_municipalities",lambda:[municipality])
    app=create_app()
    c=app.test_client()
    c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD})
    response=c.get("/api/ibge/municipios?q=Olho%20d%27Agua")
    assert response.status_code==200
    result=response.get_json()["municipios"][0]
    assert result["codigo"]==2510402
    assert result["servidores"]==20

def test_brand_logo_is_served():
    response=create_app().test_client().get("/brand/logo.svg")
    assert response.status_code==200
    assert response.mimetype=="image/svg+xml"

def test_pwa_assets_are_public():
    c=create_app().test_client()
    assert c.get("/manifest.webmanifest").mimetype=="application/manifest+json"
    assert c.get("/sw.js").mimetype=="application/javascript"

def test_numeric_cpf_import_restores_leading_zeroes():
    assert normalize_cpf(123456789)=="00123456789"

def test_conformity_report_is_aggregate_only():
    c=create_app().test_client()
    c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD})
    response=c.get("/api/relatorios/conformidade")
    assert response.status_code==200
    data=response.get_json()
    assert data["total"]==126
    assert "campo_ausente" in data
    assert data["analises"]["conclusao_percentual"]==50.0
    assert data["analises"]["pendencias_para_revisao"]==17
    assert data["analises"]["principal_cargo"]["quantidade"]==25
    assert not {"items","records","nome","cpf"}.intersection(data)

def test_executive_pdf_requires_authentication_and_returns_pdf():
    app=create_app()
    c=app.test_client()
    assert c.get("/api/relatorios/executivo.pdf").status_code==401
    assert c.post("/api/auth/login",json={"username":"josy","password":TEST_PASSWORD}).status_code==200
    response=c.get("/api/relatorios/executivo.pdf")
    assert response.status_code==200
    assert response.mimetype=="application/pdf"
    assert response.data.startswith(b"%PDF")
    assert "relatorio-executivo-conformidade.pdf" in response.headers["Content-Disposition"]
