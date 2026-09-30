from flask import Blueprint, jsonify, request, current_app, send_file
from flask_jwt_extended import create_access_token, decode_token, jwt_required, get_jwt, get_jwt_identity, verify_jwt_in_request, set_access_cookies, unset_jwt_cookies
from sqlalchemy import or_, func
from datetime import datetime, timedelta
from pathlib import Path
from werkzeug.utils import secure_filename
import csv, gzip, hashlib, io, json, os, re, threading, time, unicodedata, urllib.request, uuid
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db, jwt
from .models import User, Recenseado, Dependente, Beneficio, Contribuicao, Documento, Atendimento, Pendencia, PendenciaImportada, Auditoria, Importacao, PastaArquivo, Arquivo, LoginAttempt, AuthSession
from .utils import digits, normalize_cpf, mask_cpf, valid_cpf, parse_date, parse_datetime, iso, money

bp=Blueprint("api",__name__)
_IBGE_CACHE={"loaded_at":0,"municipios":None}
_IBGE_LOCK=threading.Lock()

def _normalize(value):
    return re.sub(r"[^a-z0-9]","",unicodedata.normalize("NFKD",str(value or "").lower()).encode("ascii","ignore").decode())

def _fetch_ibge_municipalities():
    request=urllib.request.Request(
        "https://servicodados.ibge.gov.br/api/v1/localidades/municipios",
        headers={"Accept-Encoding":"gzip","User-Agent":"CensoPrevidenciario/1.0"},
    )
    with urllib.request.urlopen(request,timeout=15) as response:
        payload=response.read()
    if payload[:2]==bytes([31,139]):
        payload=gzip.decompress(payload)
    return json.loads(payload)

def _ibge_municipalities():
    if _IBGE_CACHE["municipios"] and time.monotonic()-_IBGE_CACHE["loaded_at"]<86400:
        return _IBGE_CACHE["municipios"]
    with _IBGE_LOCK:
        if _IBGE_CACHE["municipios"] and time.monotonic()-_IBGE_CACHE["loaded_at"]<86400:
            return _IBGE_CACHE["municipios"]
        try:
            _IBGE_CACHE["municipios"]=_fetch_ibge_municipalities()
            _IBGE_CACHE["loaded_at"]=time.monotonic()
        except Exception:
            if not _IBGE_CACHE["municipios"]:
                raise
    return _IBGE_CACHE["municipios"]

def auth_guard():
    if current_app.config["CENSO_READS_REQUIRE_AUTH"]:
        verify_jwt_in_request()

def audit(acao, entidade=None, entidade_id=None, detalhes=""):
    try:
        user=get_jwt_identity()
    except Exception: user="anonimo"
    ip=request.headers.get("X-Forwarded-For",request.remote_addr)
    db.session.add(Auditoria(usuario=str(user or "anonimo"),acao=acao,entidade=entidade,entidade_id=entidade_id,ip=ip,detalhes=detalhes))
    db.session.commit()

def body():
    return request.get_json(silent=True) or {}

def rec_to_dict(x, masked=True):
    return {
        "id":x.id,"matricula":x.matricula,"cpf":mask_cpf(x.cpf) if masked else x.cpf,"nome":x.nome,
        "nome_mae":x.nome_mae,"data_nascimento":iso(x.data_nascimento),"sexo":x.sexo,
        "estado_civil":x.estado_civil,"entidade":x.entidade,"cargo":x.cargo,"tipo_vinculo":x.tipo_vinculo,
        "situacao":x.situacao,"status_recenseamento":x.status_recenseamento,
        "data_cadastro":iso(x.data_cadastro),"data_conclusao":iso(x.data_conclusao),
        "ultimo_acesso":iso(x.ultimo_acesso),"telefone":x.telefone,"email":x.email,"endereco":x.endereco,
        "cidade":x.cidade,"uf":x.uf,"observacoes":x.observacoes,
        "dependentes":[{"id":d.id,"nome":d.nome,"cpf":mask_cpf(d.cpf) if masked else d.cpf,"parentesco":d.parentesco,"data_nascimento":iso(d.data_nascimento),"dependente_previdenciario":d.dependente_previdenciario,"invalidez":d.invalidez} for d in x.dependentes],
        "beneficios":[{"id":b.id,"tipo":b.tipo,"numero":b.numero,"especie":b.especie,"inicio":iso(b.inicio),"valor":money(b.valor),"status":b.status} for b in x.beneficios]
    }

@bp.get("/")
def index():
    try:
        verify_jwt_in_request(optional=True,locations=["cookies"])
        payload=get_jwt()
    except Exception:
        payload={}
    if payload:
        session=db.session.get(AuthSession,payload["jti"])
        if session and not session.revogada_em:
            session.revogada_em=datetime.utcnow()
            db.session.commit()
    response=send_file(Path(current_app.root_path) / "index.html")
    unset_jwt_cookies(response)
    response.headers["Cache-Control"]="no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"]="no-cache"
    return response

@bp.get("/brand/logo.svg")
def brand_logo(): return send_file(Path(current_app.root_path) / "logo-josy.svg",mimetype="image/svg+xml")

@bp.get("/health")
def health(): return jsonify(status="ok",service="censo-previdenciario",time=datetime.utcnow().isoformat())

@bp.get("/api/status")
def api_status():
    response=jsonify(mensagem="API Python rodando!",status="ok")
    response.headers["Cache-Control"]="no-store"
    return response

@bp.post("/api/auth/login")
def login():
    b=body(); login_value=str(b.get("username") or "").strip().lower()
    email_aliases={
        str(os.getenv("CENSO_LUIZ_EMAIL") or "").strip().lower():"luizarrow3",
        str(os.getenv("CENSO_JOSY_EMAIL") or "").strip().lower():"josy",
        str(os.getenv("CENSO_LUIZ_USERNAME") or "").strip().lower():"luizarrow3",
        str(os.getenv("CENSO_JOSY_USERNAME") or "").strip().lower():"josy",
    }
    username=email_aliases.get(login_value,login_value)
    if username not in {"luizarrow3", "josy"}:
        return jsonify(error="Credenciais inválidas"),401
    ip=request.remote_addr or "unknown"
    attempt=LoginAttempt.query.filter_by(username=username,ip=ip).first()
    now=datetime.utcnow()
    if attempt and attempt.bloqueado_ate and attempt.bloqueado_ate>now:
        return jsonify(error="Acesso temporariamente bloqueado"),429
    u=User.query.filter_by(username=username).first()
    if not u or not u.active or not u.check_password(b.get("password","")):
        if not attempt:
            attempt=LoginAttempt(username=username,ip=ip,tentativas=0)
            db.session.add(attempt)
        if attempt.bloqueado_ate and attempt.bloqueado_ate<=now:
            attempt.tentativas=0
        attempt.tentativas+=1
        if attempt.tentativas>=5:
            attempt.bloqueado_ate=now+timedelta(minutes=15)
        db.session.commit()
        return jsonify(error="Credenciais inválidas"),401
    if attempt:
        db.session.delete(attempt)
        db.session.commit()
    token=create_access_token(identity=u.username, additional_claims={"role":u.role})
    token_payload=decode_token(token)
    db.session.add(AuthSession(
        jti=token_payload["jti"],username=u.username,
        expira_em=datetime.utcfromtimestamp(token_payload["exp"]),
    ))
    db.session.commit()
    response=jsonify(ok=True)
    set_access_cookies(response, token)
    return response

@bp.post("/api/auth/logout")
@jwt_required()
def logout():
    session=db.session.get(AuthSession,get_jwt()["jti"])
    if session and not session.revogada_em:
        session.revogada_em=datetime.utcnow()
        db.session.commit()
    response=jsonify(ok=True)
    unset_jwt_cookies(response)
    return response

@jwt.token_in_blocklist_loader
def is_session_revoked(jwt_header,jwt_payload):
    session=db.session.get(AuthSession,jwt_payload["jti"])
    return session is None or session.revogada_em is not None

@bp.get("/api/arquivos")
@jwt_required()
def list_files():
    folder_id=request.args.get("pasta_id", type=int)
    if folder_id is not None and not db.session.get(PastaArquivo, folder_id):
        return jsonify(error="Pasta não encontrada"),404
    folders=PastaArquivo.query.filter_by(pasta_pai_id=folder_id).order_by(PastaArquivo.nome).all()
    files=Arquivo.query.filter_by(pasta_id=folder_id).order_by(Arquivo.nome).all()
    breadcrumb=[]
    current=db.session.get(PastaArquivo,folder_id) if folder_id is not None else None
    while current:
        breadcrumb.insert(0,{"id":current.id,"nome":current.nome})
        current=db.session.get(PastaArquivo,current.pasta_pai_id) if current.pasta_pai_id else None
    return jsonify(
        caminho=breadcrumb,
        pastas=[{"id":x.id,"nome":x.nome} for x in folders],
        arquivos=[{"id":x.id,"nome":x.nome,"tamanho":x.tamanho,"tipo":x.tipo_mime,"criado_em":iso(x.criado_em)} for x in files],
    )

@bp.get("/api/pendencias")
@jwt_required()
def list_imported_pendencias():
    query=PendenciaImportada.query
    search=request.args.get("q","").strip()
    if search:
        term=f"%{search}%"
        query=query.filter(or_(PendenciaImportada.servidor.ilike(term),PendenciaImportada.matricula.ilike(term),PendenciaImportada.pendencia.ilike(term),PendenciaImportada.tipo_funcional.ilike(term),PendenciaImportada.observacao.ilike(term)))
    rows=query.order_by(PendenciaImportada.servidor).limit(500).all()
    status_counts={key:count for key,count in db.session.query(PendenciaImportada.status,func.count(PendenciaImportada.id)).group_by(PendenciaImportada.status).all()}
    return jsonify(total=query.count(),por_status=status_counts,items=[{
        "id":x.id,"matricula":x.matricula,"servidor":x.servidor,"pendencia":x.pendencia,
        "tipo_funcional":x.tipo_funcional,"observacao":x.observacao,"recenseado_id":x.recenseado_id,
        "municipio":x.municipio,"codigo_ibge":x.codigo_ibge,"uf":x.uf,"status":x.status,
    } for x in rows])

@bp.get("/api/relatorios/conformidade")
@jwt_required()
def report_conformity():
    total=Recenseado.query.count()
    completed=Recenseado.query.filter(Recenseado.data_conclusao.isnot(None)).count()
    status_counts={key:count for key,count in db.session.query(Recenseado.status_recenseamento,func.count(Recenseado.id)).group_by(Recenseado.status_recenseamento).all()}
    category_counts={key or "Não informado":count for key,count in db.session.query(Recenseado.tipo_vinculo,func.count(Recenseado.id)).group_by(Recenseado.tipo_vinculo).all()}
    gender_counts={key or "Não informado":count for key,count in db.session.query(Recenseado.sexo,func.count(Recenseado.id)).group_by(Recenseado.sexo).all()}
    missing_fields={
        "cpf":Recenseado.query.filter(or_(Recenseado.cpf.is_(None),Recenseado.cpf=="")).count(),
        "data_nascimento":Recenseado.query.filter(Recenseado.data_nascimento.is_(None)).count(),
        "sexo":Recenseado.query.filter(or_(Recenseado.sexo.is_(None),Recenseado.sexo=="")).count(),
        "cargo":Recenseado.query.filter(or_(Recenseado.cargo.is_(None),Recenseado.cargo=="")).count(),
        "municipio_uf":Recenseado.query.filter(or_(Recenseado.cidade.is_(None),Recenseado.uf.is_(None))).count(),
    }
    pending_counts={key:count for key,count in db.session.query(PendenciaImportada.status,func.count(PendenciaImportada.id)).group_by(PendenciaImportada.status).all()}
    pending_total=PendenciaImportada.query.count()
    pending_linked=PendenciaImportada.query.filter(PendenciaImportada.recenseado_id.isnot(None)).count()
    top_cargo=db.session.query(Recenseado.cargo,func.count(Recenseado.id)).filter(Recenseado.cargo.isnot(None),Recenseado.cargo!="").group_by(Recenseado.cargo).order_by(func.count(Recenseado.id).desc()).first()
    duplicate_cpf_groups=db.session.query(Recenseado.cpf).filter(Recenseado.cpf.isnot(None),Recenseado.cpf!="").group_by(Recenseado.cpf).having(func.count(Recenseado.id)>1).count()
    duplicate_matricula_groups=db.session.query(Recenseado.matricula).filter(Recenseado.matricula.isnot(None),Recenseado.matricula!="").group_by(Recenseado.matricula).having(func.count(Recenseado.id)>1).count()
    cadastro_counts={str(key):value for key,value in db.session.query(func.date(Recenseado.data_cadastro),func.count(Recenseado.id)).group_by(func.date(Recenseado.data_cadastro)).order_by(func.date(Recenseado.data_cadastro)).all()}
    return jsonify(
        total=total,por_status=status_counts,por_tipo_funcional=category_counts,por_sexo=gender_counts,
        campo_ausente=missing_fields,pendencias=pending_counts,
        analises={
            "conclusao_percentual":round(completed/total*100,1) if total else 0,
            "cobertura_cpf_percentual":round((total-missing_fields["cpf"])/total*100,1) if total else 0,
            "cobertura_localidade_percentual":round((total-missing_fields["municipio_uf"])/total*100,1) if total else 0,
            "pendencias_vinculadas_percentual":round(pending_linked/pending_total*100,1) if pending_total else 0,
            "pendencias_para_revisao":pending_total-pending_linked,
            "duplicidades_cpf":duplicate_cpf_groups,
            "duplicidades_matricula":duplicate_matricula_groups,
            "principal_cargo":{"nome":top_cargo[0] if top_cargo else "Não informado","quantidade":top_cargo[1] if top_cargo else 0},
            "cadastros_por_data":cadastro_counts,
        },
    )

@bp.get("/api/relatorios/executivo.pdf")
@jwt_required()
def executive_report_pdf():
    total=Recenseado.query.count()
    completed=Recenseado.query.filter(Recenseado.data_conclusao.isnot(None)).count()
    status_counts={key or "Não informado":count for key,count in db.session.query(Recenseado.status_recenseamento,func.count(Recenseado.id)).group_by(Recenseado.status_recenseamento).all()}
    cargo_counts={key or "Não informado":count for key,count in db.session.query(Recenseado.cargo,func.count(Recenseado.id)).group_by(Recenseado.cargo).order_by(func.count(Recenseado.id).desc()).limit(10).all()}
    pending_total=PendenciaImportada.query.count()
    pending_linked=PendenciaImportada.query.filter(PendenciaImportada.recenseado_id.isnot(None)).count()
    missing_fields={
        "CPF":Recenseado.query.filter(or_(Recenseado.cpf.is_(None),Recenseado.cpf=="")).count(),
        "Data de nascimento":Recenseado.query.filter(Recenseado.data_nascimento.is_(None)).count(),
        "Sexo":Recenseado.query.filter(or_(Recenseado.sexo.is_(None),Recenseado.sexo=="")).count(),
        "Cidade/UF":Recenseado.query.filter(or_(Recenseado.cidade.is_(None),Recenseado.uf.is_(None))).count(),
    }
    pending_counts={key or "Não informado":count for key,count in db.session.query(PendenciaImportada.status,func.count(PendenciaImportada.id)).group_by(PendenciaImportada.status).all()}
    duplicate_cpf_groups=db.session.query(Recenseado.cpf).filter(Recenseado.cpf.isnot(None),Recenseado.cpf!="").group_by(Recenseado.cpf).having(func.count(Recenseado.id)>1).count()
    duplicate_matricula_groups=db.session.query(Recenseado.matricula).filter(Recenseado.matricula.isnot(None),Recenseado.matricula!="").group_by(Recenseado.matricula).having(func.count(Recenseado.id)>1).count()

    buffer=io.BytesIO()
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#174d5e"), spaceAfter=5))
    styles.add(ParagraphStyle(name="ReportNote", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#52656b")))
    document=SimpleDocTemplate(buffer, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=15*mm, bottomMargin=15*mm)
    story=[
        Paragraph("Relatório Executivo de Conformidade", styles["ReportTitle"]),
        Paragraph("Censo Previdenciário · visão agregada sem nomes, CPFs ou matrículas", styles["ReportNote"]),
        Spacer(1, 8*mm),
    ]

    def add_table(title, rows):
        story.append(Paragraph(title, styles["Heading2"]))
        table=Table([["Indicador", "Quantidade"]]+rows, colWidths=[125*mm, 35*mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174d5e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#bdcac6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f1")]),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([table, Spacer(1, 6*mm)])

    add_table("KPIs principais", [
        ["Registros no censo", str(total)],
        ["Registros concluídos", str(completed)],
        ["Registros sem conclusão", str(total-completed)],
        ["Pendências importadas", str(pending_total)],
        ["Pendências vinculadas", str(pending_linked)],
        ["Pendências para revisão", str(pending_total-pending_linked)],
    ])
    add_table("Análises estatísticas", [
        ["Conclusão cadastral", f"{completed/total*100:.1f}%" if total else "0,0%"],
        ["Cobertura de CPF", f"{(total-missing_fields['CPF'])/total*100:.1f}%" if total else "0,0%"],
        ["Cobertura de cidade/UF", f"{(total-missing_fields['Cidade/UF'])/total*100:.1f}%" if total else "0,0%"],
        ["Pendências vinculadas", f"{pending_linked/pending_total*100:.1f}%" if pending_total else "0,0%"],
        ["Grupos com CPF duplicado", str(duplicate_cpf_groups)],
        ["Grupos com matrícula duplicada", str(duplicate_matricula_groups)],
    ])
    add_table("Registros por status", [[str(key), str(value)] for key,value in status_counts.items()])
    add_table("Campos ausentes", [[str(key), str(value)] for key,value in missing_fields.items()])
    add_table("Pendências por status", [[str(key), str(value)] for key,value in pending_counts.items()])
    add_table("Dez maiores categorias de cargo", [[str(key), str(value)] for key,value in cargo_counts.items()])
    story.append(Paragraph("Observação: este documento apresenta contagens agregadas da base atual. Os dados de folha exibidos no dashboard não foram incluídos por não estarem armazenados em registros detalhados de benefícios ou contribuições.", styles["ReportNote"]))
    document.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name="relatorio-executivo-conformidade.pdf")

@bp.get("/api/ibge/municipios")
@jwt_required()
def search_ibge_municipalities():
    search=request.args.get("q","").strip()[:100]
    normalized=_normalize(search)
    if len(normalized)<2:
        return jsonify(error="Informe pelo menos duas letras para buscar"),400
    try:
        municipalities=_ibge_municipalities()
    except Exception:
        current_app.logger.exception("Falha ao consultar o catálogo de localidades do IBGE")
        return jsonify(error="A consulta ao IBGE está indisponível no momento"),502

    functional_counts={}
    records=Recenseado.query.with_entities(Recenseado.cidade,Recenseado.uf,Recenseado.tipo_vinculo).all()
    missing_location=sum(not city or not uf for city,uf,_ in records)
    for city,uf,kind in records:
        if city and uf:
            key=(_normalize(city),str(uf).upper())
            functional_counts.setdefault(key,{"total":0,"tipos":{}})
            functional_counts[key]["total"]+=1
            category=kind or "Não informado"
            functional_counts[key]["tipos"][category]=functional_counts[key]["tipos"].get(category,0)+1
    unlinked_pending=PendenciaImportada.query.filter(PendenciaImportada.recenseado_id.is_(None)).all()
    for pending in unlinked_pending:
        key=(_normalize(pending.municipio),pending.uf.upper())
        functional_counts.setdefault(key,{"total":0,"tipos":{}})
        functional_counts[key]["total"]+=1
        category=pending.tipo_funcional or "Não informado"
        functional_counts[key]["tipos"][category]=functional_counts[key]["tipos"].get(category,0)+1

    matches=[]
    for item in municipalities:
        name=item.get("nome","")
        name_key=_normalize(name)
        if normalized not in name_key:
            continue
        region=item.get("microrregiao",{}).get("mesorregiao",{}).get("UF")
        if not region:
            region=item.get("regiao-imediata",{}).get("regiao-intermediaria",{}).get("UF",{})
        uf=region.get("sigla","")
        count=functional_counts.get((name_key,uf),{"total":0,"tipos":{}})
        matches.append({
            "codigo":item.get("id"),"nome":name,"uf":uf,
            "regiao":region.get("regiao",{}).get("nome",""),
            "servidores":count["total"],"por_tipo":count["tipos"],
        })
    matches.sort(key=lambda item:(0 if _normalize(item["nome"])==normalized else 1,item["nome"],item["uf"]))
    return jsonify(municipios=matches[:25],total_base=len(records),pendencias_importadas=PendenciaImportada.query.count(),sem_localidade=missing_location)

@bp.post("/api/arquivos/pastas")
@jwt_required()
def create_folder():
    b=body(); name=str(b.get("nome","")).strip(); parent_id=b.get("pasta_pai_id")
    if not name or len(name)>180 or "/" in name or "\\" in name or name in {".",".."}:
        return jsonify(error="Nome de pasta inválido"),400
    if parent_id is not None and not db.session.get(PastaArquivo, parent_id):
        return jsonify(error="Pasta pai não encontrada"),404
    folder=PastaArquivo(nome=name,pasta_pai_id=parent_id)
    db.session.add(folder);db.session.commit()
    return jsonify(id=folder.id,nome=folder.nome),201

@bp.post("/api/arquivos/upload")
@jwt_required()
def upload_file():
    uploaded=request.files.get("arquivo")
    if not uploaded or not uploaded.filename:
        return jsonify(error="Selecione um arquivo"),400
    filename=secure_filename(uploaded.filename)
    if not filename:
        return jsonify(error="Nome de arquivo inválido"),400
    folder_id=request.form.get("pasta_id", type=int)
    if folder_id is not None and not db.session.get(PastaArquivo, folder_id):
        return jsonify(error="Pasta não encontrada"),404

    storage=Path(current_app.config["UPLOAD_FOLDER"])
    stored_name=f"{uuid.uuid4().hex}.blob"
    temporary=storage / f".{stored_name}.part"
    destination=storage / stored_name
    size=0
    try:
        with temporary.open("wb") as output:
            while chunk:=uploaded.stream.read(1024*1024):
                size+=len(chunk)
                if size>current_app.config["MAX_FILE_SIZE"]:
                    raise ValueError("O limite por arquivo é 6 GB")
                output.write(chunk)
        os.replace(temporary,destination)
        item=Arquivo(nome=filename,nome_armazenado=stored_name,tamanho=size,tipo_mime=uploaded.mimetype,pasta_id=folder_id,usuario=get_jwt_identity())
        db.session.add(item);db.session.commit()
    except ValueError as error:
        temporary.unlink(missing_ok=True);destination.unlink(missing_ok=True)
        return jsonify(error=str(error)),413
    except Exception:
        db.session.rollback();temporary.unlink(missing_ok=True);destination.unlink(missing_ok=True)
        current_app.logger.exception("Falha ao salvar arquivo enviado")
        return jsonify(error="Não foi possível salvar o arquivo"),500
    return jsonify(id=item.id,nome=item.nome,tamanho=item.tamanho),201

@bp.get("/api/arquivos/<int:file_id>/download")
@jwt_required()
def download_file(file_id):
    item=db.session.get(Arquivo,file_id)
    if not item:return jsonify(error="Arquivo não encontrado"),404
    path=Path(current_app.config["UPLOAD_FOLDER"])/item.nome_armazenado
    if not path.is_file():return jsonify(error="Arquivo não encontrado no armazenamento"),404
    return send_file(path,as_attachment=True,download_name=item.nome,mimetype=item.tipo_mime or "application/octet-stream")

@bp.delete("/api/arquivos/<int:file_id>")
@jwt_required()
def delete_file(file_id):
    item=db.session.get(Arquivo,file_id)
    if not item:return jsonify(error="Arquivo não encontrado"),404
    path=Path(current_app.config["UPLOAD_FOLDER"])/item.nome_armazenado
    db.session.delete(item);db.session.commit();path.unlink(missing_ok=True)
    return jsonify(ok=True)

@bp.delete("/api/arquivos/pastas/<int:folder_id>")
@jwt_required()
def delete_folder(folder_id):
    folder=db.session.get(PastaArquivo,folder_id)
    if not folder:return jsonify(error="Pasta não encontrada"),404
    if PastaArquivo.query.filter_by(pasta_pai_id=folder_id).first() or Arquivo.query.filter_by(pasta_id=folder_id).first():
        return jsonify(error="A pasta precisa estar vazia para ser excluída"),409
    db.session.delete(folder);db.session.commit()
    return jsonify(ok=True)

@bp.get("/api/dashboard")
def dashboard():
    auth_guard()
    total=Recenseado.query.count()
    concluido=Recenseado.query.filter(Recenseado.data_conclusao.isnot(None)).count()
    cargos=db.session.query(Recenseado.cargo,func.count(Recenseado.id)).group_by(Recenseado.cargo).order_by(func.count(Recenseado.id).desc()).limit(10).all()
    datas=db.session.query(func.date(Recenseado.data_cadastro),func.count(Recenseado.id)).group_by(func.date(Recenseado.data_cadastro)).order_by(func.date(Recenseado.data_cadastro)).all()
    return jsonify({
      "records":[rec_to_dict(x) for x in Recenseado.query.order_by(Recenseado.id).all()],
      "cargo_counts":{k or "Não informado":v for k,v in cargos},
      "cadastro_counts":{str(k):v for k,v in datas},
      "payroll":{"ativos":{"quantidade":76,"proventos":285978.89,"base":251822.02,"descontos":35254.71,"patronal":73834.42},
        "beneficios":[
          {"tipo":"Aposentados por idade e tempo de contribuição","quantidade":36,"vencimentos":154423.25,"descontos":25124.90,"liquido":129298.35},
          {"tipo":"Aposentados por invalidez","quantidade":4,"vencimentos":17797.41,"descontos":2222.03,"liquido":15575.38},
          {"tipo":"Pensionistas por morte","quantidade":4,"vencimentos":13511.43,"descontos":2625.43,"liquido":10886.00},
          {"tipo":"Aposentadoria por idade","quantidade":8,"vencimentos":12968.00,"descontos":1849.62,"liquido":11118.38}]},
      "censo":{"registros":total,"concluidos":concluido,"sem_conclusao":total-concluido,
        "entidades":db.session.query(Recenseado.entidade).distinct().count(),
        "data_inicial":(Recenseado.query.order_by(Recenseado.data_cadastro.asc()).first().data_cadastro.strftime("%d/%m/%Y") if total else ""),
        "data_final":(Recenseado.query.order_by(Recenseado.data_cadastro.desc()).first().data_cadastro.strftime("%d/%m/%Y") if total else "")}
    })

@bp.get("/api/recenseados")
def list_recenseados():
    auth_guard()
    q=request.args.get("q","").strip(); cargo=request.args.get("cargo"); status=request.args.get("status")
    query=Recenseado.query
    if q: query=query.filter(or_(Recenseado.nome.ilike(f"%{q}%"),Recenseado.matricula.ilike(f"%{q}%"),Recenseado.cpf.ilike(f"%{digits(q)}%")))
    if cargo: query=query.filter_by(cargo=cargo)
    if status: query=query.filter_by(status_recenseamento=status)
    page=max(int(request.args.get("page",1)),1); size=min(max(int(request.args.get("size",50)),1),500)
    total=query.count(); rows=query.order_by(Recenseado.nome).offset((page-1)*size).limit(size).all()
    return jsonify(items=[rec_to_dict(x) for x in rows],page=page,size=size,total=total)

@bp.get("/api/recenseados/<int:rid>")
def get_recenseado(rid):
    auth_guard(); x=Recenseado.query.get_or_404(rid); return jsonify(rec_to_dict(x,masked=False))

@bp.post("/api/recenseados")
@jwt_required()
def create_recenseado():
    b=body(); cpf=normalize_cpf(b.get("cpf"))
    if cpf and not valid_cpf(cpf): return jsonify(error="CPF inválido"),400
    if cpf and Recenseado.query.filter_by(cpf=cpf).first(): return jsonify(error="CPF já cadastrado"),409
    x=Recenseado(nome=b.get("nome","").strip(),cpf=cpf,matricula=b.get("matricula"),cargo=b.get("cargo"),entidade=b.get("entidade"),
        nome_mae=b.get("nome_mae"),data_nascimento=parse_date(b.get("data_nascimento")),sexo=b.get("sexo"),estado_civil=b.get("estado_civil"),
        tipo_vinculo=b.get("tipo_vinculo"),situacao=b.get("situacao","Pendente"),telefone=b.get("telefone"),email=b.get("email"),
        endereco=b.get("endereco"),cidade=b.get("cidade"),uf=b.get("uf"),observacoes=b.get("observacoes"),status_recenseamento="Em andamento")
    if not x.nome:return jsonify(error="Nome é obrigatório"),400
    db.session.add(x);db.session.commit();audit("CRIAR","Recenseado",x.id);return jsonify(rec_to_dict(x,False)),201

@bp.put("/api/recenseados/<int:rid>")
@jwt_required()
def update_recenseado(rid):
    x=Recenseado.query.get_or_404(rid); b=body()
    fields=["nome","matricula","cargo","entidade","nome_mae","sexo","estado_civil","tipo_vinculo","situacao","telefone","email","endereco","cidade","uf","observacoes"]
    for f in fields:
        if f in b:setattr(x,f,b[f])
    if "cpf" in b:
        cpf=normalize_cpf(b["cpf"])
        if cpf and not valid_cpf(cpf):return jsonify(error="CPF inválido"),400
        x.cpf=cpf
    for f in ("data_nascimento",):
        if f in b:setattr(x,f,parse_date(b[f]))
    if b.get("status_recenseamento"): x.status_recenseamento=b["status_recenseamento"]
    if x.status_recenseamento=="Concluído" and not x.data_conclusao:x.data_conclusao=datetime.utcnow()
    db.session.commit();audit("ATUALIZAR","Recenseado",x.id);return jsonify(rec_to_dict(x,False))

@bp.delete("/api/recenseados/<int:rid>")
@jwt_required()
def delete_recenseado(rid):
    x=Recenseado.query.get_or_404(rid); db.session.delete(x);db.session.commit();audit("EXCLUIR","Recenseado",rid);return jsonify(ok=True)

@bp.post("/api/recenseados/<int:rid>/concluir")
@jwt_required()
def concluir(rid):
    x=Recenseado.query.get_or_404(rid);x.status_recenseamento="Concluído";x.data_conclusao=datetime.utcnow();db.session.commit();audit("CONCLUIR","Recenseado",rid);return jsonify(rec_to_dict(x,False))

@bp.get("/api/recenseados/<int:rid>/pendencias")
def pendencias(rid):
    auth_guard(); rows=Pendencia.query.filter_by(recenseado_id=rid).order_by(Pendencia.criado_em.desc()).all()
    return jsonify([{"id":p.id,"tipo":p.tipo,"descricao":p.descricao,"severidade":p.severidade,"status":p.status,"responsavel":p.responsavel,"criado_em":iso(p.criado_em)} for p in rows])

@bp.post("/api/recenseados/<int:rid>/pendencias")
@jwt_required()
def criar_pendencia(rid):
    b=body(); Recenseado.query.get_or_404(rid)
    p=Pendencia(recenseado_id=rid,tipo=b.get("tipo","Cadastro"),descricao=b.get("descricao",""),severidade=b.get("severidade","Media"),responsavel=b.get("responsavel"))
    db.session.add(p);db.session.commit();audit("CRIAR","Pendencia",p.id);return jsonify(id=p.id),201

@bp.post("/api/importacoes/recenseados")
@jwt_required()
def import_recenseados():
    f=request.files.get("arquivo")
    if not f:return jsonify(error="Envie o arquivo no campo 'arquivo'"),400
    imp=Importacao(arquivo=f.filename,tipo="recenseados");db.session.add(imp);db.session.flush()
    try:
        df=pd.read_excel(f)
        imp.registros_lidos=len(df)
        for _,r in df.iterrows():
            matricula=str(r.get("Matrícula","")).replace(".0","").strip()
            cpf=normalize_cpf(r.get("CPF",""))
            x=Recenseado.query.filter_by(matricula=matricula).first() if matricula else None
            if not x and cpf:x=Recenseado.query.filter_by(cpf=cpf).first()
            if not x:
                x=Recenseado(matricula=matricula,cpf=cpf,nome=str(r.get("Nome Completo","")).strip())
                db.session.add(x);imp.registros_criados+=1
            else: imp.registros_atualizados+=1
            x.entidade=str(r.get("Entidade","")).strip()
            x.cargo=str(r.get("Cargo","")).strip()
            x.data_cadastro=parse_date(r.get("Data Cadastro"))
            x.data_conclusao=parse_date(r.get("Data Conclusão"))
            x.ultimo_acesso=parse_datetime(r.get("Último Acesso"))
            x.status_recenseamento="Concluído" if x.data_conclusao else "Em andamento"
        imp.status="Concluída";db.session.commit();audit("IMPORTAR","Importacao",imp.id)
        return jsonify(id=imp.id,status=imp.status,registros_lidos=imp.registros_lidos,criados=imp.registros_criados,atualizados=imp.registros_atualizados),201
    except Exception as e:
        db.session.rollback();return jsonify(error=str(e)),400

@bp.get("/api/validacoes")
def validacoes():
    auth_guard()
    sem_cpf=Recenseado.query.filter(or_(Recenseado.cpf==None,Recenseado.cpf=="")).count()
    sem_nome=Recenseado.query.filter(or_(Recenseado.nome==None,Recenseado.nome=="")).count()
    dup_matriculas=db.session.query(Recenseado.matricula,func.count(Recenseado.id)).filter(Recenseado.matricula.isnot(None),Recenseado.matricula!="").group_by(Recenseado.matricula).having(func.count(Recenseado.id)>1).all()
    dup_cpf=db.session.query(Recenseado.cpf,func.count(Recenseado.id)).filter(Recenseado.cpf.isnot(None),Recenseado.cpf!="").group_by(Recenseado.cpf).having(func.count(Recenseado.id)>1).all()
    return jsonify({"sem_cpf":sem_cpf,"sem_nome":sem_nome,"duplicidades_matricula":[{"matricula":k,"quantidade":v} for k,v in dup_matriculas],
      "duplicidades_cpf":[{"cpf":mask_cpf(k),"quantidade":v} for k,v in dup_cpf]})

@bp.get("/api/relatorios/recenseados.csv")
def export_csv():
    auth_guard(); out=io.StringIO(); w=csv.writer(out);w.writerow(["id","matricula","cpf","nome","entidade","cargo","status","data_cadastro","data_conclusao"])
    for x in Recenseado.query.order_by(Recenseado.nome):w.writerow([x.id,x.matricula,mask_cpf(x.cpf),x.nome,x.entidade,x.cargo,x.status_recenseamento,iso(x.data_cadastro),iso(x.data_conclusao)])
    data=io.BytesIO(out.getvalue().encode("utf-8-sig"));return send_file(data,mimetype="text/csv",as_attachment=True,download_name="recenseados.csv")

@bp.get("/api/auditoria")
@jwt_required()
def auditoria():
    rows=Auditoria.query.order_by(Auditoria.criado_em.desc()).limit(200).all()
    return jsonify([{"id":x.id,"usuario":x.usuario,"acao":x.acao,"entidade":x.entidade,"entidade_id":x.entidade_id,"ip":x.ip,"detalhes":x.detalhes,"criado_em":iso(x.criado_em)} for x in rows])

# CRUD genérico para dependentes, benefícios, contribuições e atendimentos
@bp.post("/api/recenseados/<int:rid>/dependentes")
@jwt_required()
def add_dep(rid):
    Recenseado.query.get_or_404(rid);b=body();d=Dependente(recenseado_id=rid,nome=b.get("nome",""),cpf=normalize_cpf(b.get("cpf")),parentesco=b.get("parentesco"),data_nascimento=parse_date(b.get("data_nascimento")),dependente_previdenciario=bool(b.get("dependente_previdenciario")),invalidez=bool(b.get("invalidez")),observacoes=b.get("observacoes"))
    if not d.nome:return jsonify(error="Nome obrigatório"),400
    db.session.add(d);db.session.commit();audit("CRIAR","Dependente",d.id);return jsonify(id=d.id),201

@bp.post("/api/recenseados/<int:rid>/beneficios")
@jwt_required()
def add_beneficio(rid):
    Recenseado.query.get_or_404(rid);b=body();x=Beneficio(recenseado_id=rid,tipo=b.get("tipo",""),numero=b.get("numero"),especie=b.get("especie"),inicio=parse_date(b.get("inicio")),valor=b.get("valor",0),status=b.get("status","Ativo"),observacoes=b.get("observacoes"))
    if not x.tipo:return jsonify(error="Tipo obrigatório"),400
    db.session.add(x);db.session.commit();audit("CRIAR","Beneficio",x.id);return jsonify(id=x.id),201

@bp.post("/api/recenseados/<int:rid>/contribuicoes")
@jwt_required()
def add_contrib(rid):
    Recenseado.query.get_or_404(rid);b=body();x=Contribuicao(recenseado_id=rid,competencia=b.get("competencia",""),base=b.get("base",0),desconto_segurado=b.get("desconto_segurado",0),patronal=b.get("patronal",0),proventos=b.get("proventos",0),status=b.get("status","Manual"))
    if not x.competencia:return jsonify(error="Competência obrigatória"),400
    db.session.add(x);db.session.commit();audit("CRIAR","Contribuicao",x.id);return jsonify(id=x.id),201

@bp.post("/api/recenseados/<int:rid>/atendimentos")
@jwt_required()
def add_atendimento(rid):
    Recenseado.query.get_or_404(rid);b=body();x=Atendimento(recenseado_id=rid,data_hora=parse_datetime(b.get("data_hora")) or datetime.utcnow(),canal=b.get("canal","Presencial"),local=b.get("local"),atendente=b.get("atendente"),status=b.get("status","Agendado"),observacoes=b.get("observacoes"))
    db.session.add(x);db.session.commit();audit("CRIAR","Atendimento",x.id);return jsonify(id=x.id),201

@bp.get("/api/metadados")
def metadados():
    auth_guard()
    cargos=[x[0] for x in db.session.query(Recenseado.cargo).distinct().order_by(Recenseado.cargo).all() if x[0]]
    entidades=[x[0] for x in db.session.query(Recenseado.entidade).distinct().order_by(Recenseado.entidade).all() if x[0]]
    return jsonify(cargos=cargos,entidades=entidades,status=["Em andamento","Concluído","Pendente","Em análise"],uf=list("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()))

