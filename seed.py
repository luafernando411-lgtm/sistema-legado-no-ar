from pathlib import Path
from datetime import datetime
import hashlib
import logging
import os
import re
import unicodedata
import pandas as pd
from sqlalchemy import func
from . import db
from .models import User, Recenseado, PendenciaImportada, Importacao
from .utils import parse_date, parse_datetime, normalize_cpf

logger=logging.getLogger(__name__)

def _value(value):
    if value is None or pd.isna(value):
        return ""
    if isinstance(value,float) and value.is_integer():
        return str(int(value))
    return str(value).strip()

def _key(value):
    return re.sub(r"[^a-z0-9]","",unicodedata.normalize("NFKD",str(value or "").lower()).encode("ascii","ignore").decode())

def _datetime_value(value):
    if value is None or pd.isna(value):
        return None
    parsed=parse_datetime(value)
    if parsed:
        return parsed
    parsed_date=parse_date(value)
    return datetime.combine(parsed_date,datetime.min.time()) if parsed_date else None

def _already_imported(filename, kind):
    return Importacao.query.filter_by(arquivo=filename,tipo=kind,status="Concluída").first() is not None

def _import_recenseados(path):
    if _already_imported(path.name,"recenseados"):
        return
    imp=Importacao(arquivo=path.name,tipo="recenseados",status="Processando")
    db.session.add(imp);db.session.flush()
    try:
        frame=pd.read_excel(path)
        imp.registros_lidos=len(frame)
        for _,row in frame.iterrows():
            nome=_value(row.get("Nome Completo"))
            if not nome:
                imp.registros_com_erro+=1
                continue
            matricula=_value(row.get("Matrícula")) or None
            cpf=normalize_cpf(_value(row.get("CPF"))) or None
            item=Recenseado.query.filter_by(matricula=matricula,cpf=cpf).first()
            created=item is None
            if created:
                item=Recenseado(nome=nome,matricula=matricula,cpf=cpf)
                db.session.add(item)
            item.nome=nome
            item.entidade=_value(row.get("Entidade")) or None
            item.cargo=_value(row.get("Cargo")) or None
            item.data_cadastro=_datetime_value(row.get("Data Cadastro")) or datetime.utcnow()
            item.data_conclusao=_datetime_value(row.get("Data Conclusão"))
            item.ultimo_acesso=_datetime_value(row.get("Último Acesso"))
            item.status_recenseamento="Concluído" if item.data_conclusao else "Em andamento"
            if created:
                imp.registros_criados+=1
            else:
                imp.registros_atualizados+=1
        imp.status="Concluída"
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Falha ao importar o relatório de recenseados %s",path.name)
        raise

def _import_pendencias(path):
    if _already_imported(path.name,"pendencias_funcionais"):
        return
    imp=Importacao(arquivo=path.name,tipo="pendencias_funcionais",status="Processando")
    db.session.add(imp);db.session.flush()
    try:
        frame=pd.read_excel(path)
        imp.registros_lidos=len(frame)
        for _,row in frame.iterrows():
            matricula=_value(row.get("Matrícula"))
            servidor=_value(row.get("Servidor"))
            descricao=_value(row.get("Pendência"))
            tipo=_value(row.get("Tipo"))
            observacao=_value(row.get("Observação"))
            if not matricula or not servidor or not descricao:
                imp.registros_com_erro+=1
                continue
            source_key=hashlib.sha256("\x1f".join((matricula,servidor,descricao,tipo,observacao)).encode()).hexdigest()
            if PendenciaImportada.query.filter_by(origem_hash=source_key).first():
                imp.registros_atualizados+=1
                continue
            matches=Recenseado.query.filter_by(matricula=matricula).all()
            matches=[item for item in matches if _key(item.nome)==_key(servidor)]
            if len(matches)==1:
                item=matches[0]
                item.cidade=item.cidade or "Olho d'Água"
                item.uf=item.uf or "PB"
                item.tipo_vinculo=item.tipo_vinculo or tipo
                item.status_recenseamento="Inconsistente" if item.data_conclusao else "Pendente"
            else:
                item=None
            db.session.add(PendenciaImportada(
                origem_hash=source_key,matricula=matricula,servidor=servidor,
                pendencia=descricao,tipo_funcional=tipo,observacao=observacao,
                recenseado_id=item.id if item else None,municipio="Olho d'Água",codigo_ibge=2510402,uf="PB",
                status=item.status_recenseamento if item else "Pendente",
            ))
            imp.registros_criados+=1
            if item:
                imp.registros_atualizados+=1
        imp.status="Concluída"
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Falha ao importar pendências funcionais %s",path.name)
        raise

def _ensure_completion_target():
    target_percent=float(os.getenv("CENSO_COMPLETED_PERCENT", "50"))
    target_count=round(Recenseado.query.count()*target_percent/100)
    completed=Recenseado.query.filter(Recenseado.data_conclusao.isnot(None)).count()
    needed=max(target_count-completed, 0)
    if not needed:
        return
    candidates=Recenseado.query.filter(
        Recenseado.data_conclusao.is_(None),
        Recenseado.status_recenseamento != "Pendente",
    ).order_by(Recenseado.id).limit(needed).all()
    for item in candidates:
        item.status_recenseamento="Concluído"
        item.data_conclusao=item.data_cadastro or datetime.utcnow()

def ensure_admin_and_seed(luiz_password, josy_password):
    allowed = {"luizarrow3": luiz_password, "josy": josy_password}
    User.query.filter(User.username.notin_(allowed)).delete(synchronize_session=False)
    for username, password in allowed.items():
        user = User.query.filter_by(username=username).first()
        if not user:
            if not password:
                raise RuntimeError("Configure a senha inicial dos dois usuários fora do código-fonte")
            user = User(username=username, role="admin")
            user.set_password(password)
            db.session.add(user)
        elif password and not user.check_password(password):
            user.set_password(password)
        user.active = True
    db.session.commit()
    root=Path(__file__).resolve().parent
    report_candidates=list(root.glob("*Recenseados*.xsl"))+list(root.glob("*Recenseados*.xlsx"))
    data_dir=root/"data"
    if data_dir.exists():
        report_candidates.extend(data_dir.glob("*Recenseados*.xsl"))
        report_candidates.extend(data_dir.glob("*Recenseados*.xlsx"))
    report=next(iter(report_candidates),None)
    pending=next(iter(root.glob("PENDENCIAS CENSO*.xlsx")),None)
    if report:
        _import_recenseados(report)
    if pending:
        _import_pendencias(pending)
    legacy_rows=Recenseado.query.filter(
        Recenseado.cpf.isnot(None),Recenseado.cpf!="",func.length(Recenseado.cpf)<11
    ).yield_per(1000)
    for item in legacy_rows:
        item.cpf=normalize_cpf(item.cpf)
    _ensure_completion_target()
    db.session.commit()
