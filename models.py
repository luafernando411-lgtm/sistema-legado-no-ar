from datetime import datetime, date
from . import db
from werkzeug.security import generate_password_hash

class User(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    username=db.Column(db.String(80), unique=True, nullable=False)
    password_hash=db.Column(db.String(255), nullable=False)
    role=db.Column(db.String(30), nullable=False, default="operador")
    active=db.Column(db.Boolean, default=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password): self.password_hash=generate_password_hash(password)
    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash,password)

class LoginAttempt(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    username=db.Column(db.String(80), nullable=False)
    ip=db.Column(db.String(80), nullable=False)
    tentativas=db.Column(db.Integer, nullable=False, default=0)
    bloqueado_ate=db.Column(db.DateTime)
    __table_args__=(db.UniqueConstraint("username","ip"),)

class AuthSession(db.Model):
    jti=db.Column(db.String(36), primary_key=True)
    username=db.Column(db.String(80), nullable=False)
    expira_em=db.Column(db.DateTime, nullable=False)
    revogada_em=db.Column(db.DateTime)

class Recenseado(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    matricula=db.Column(db.String(40), index=True)
    cpf=db.Column(db.String(14), index=True)
    nome=db.Column(db.String(180), nullable=False)
    nome_mae=db.Column(db.String(180))
    data_nascimento=db.Column(db.Date)
    sexo=db.Column(db.String(20))
    estado_civil=db.Column(db.String(40))
    entidade=db.Column(db.String(180))
    cargo=db.Column(db.String(180))
    tipo_vinculo=db.Column(db.String(80))
    situacao=db.Column(db.String(60), default="Pendente")
    status_recenseamento=db.Column(db.String(40), default="Em andamento")
    data_cadastro=db.Column(db.DateTime, default=datetime.utcnow)
    data_conclusao=db.Column(db.DateTime)
    ultimo_acesso=db.Column(db.DateTime)
    telefone=db.Column(db.String(40))
    email=db.Column(db.String(180))
    endereco=db.Column(db.Text)
    cidade=db.Column(db.String(100))
    uf=db.Column(db.String(2))
    observacoes=db.Column(db.Text)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)
    updated_at=db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    dependentes=db.relationship("Dependente", backref="recenseado", cascade="all,delete-orphan")
    documentos=db.relationship("Documento", backref="recenseado", cascade="all,delete-orphan")
    beneficios=db.relationship("Beneficio", backref="recenseado", cascade="all,delete-orphan")

class Dependente(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"), nullable=False)
    nome=db.Column(db.String(180), nullable=False)
    cpf=db.Column(db.String(14))
    parentesco=db.Column(db.String(60))
    data_nascimento=db.Column(db.Date)
    dependente_previdenciario=db.Column(db.Boolean, default=False)
    invalidez=db.Column(db.Boolean, default=False)
    observacoes=db.Column(db.Text)

class Beneficio(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"))
    tipo=db.Column(db.String(100), nullable=False)
    numero=db.Column(db.String(60))
    especie=db.Column(db.String(60))
    inicio=db.Column(db.Date)
    valor=db.Column(db.Numeric(14,2), default=0)
    status=db.Column(db.String(40), default="Ativo")
    observacoes=db.Column(db.Text)

class Contribuicao(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"))
    competencia=db.Column(db.String(7), nullable=False)
    base=db.Column(db.Numeric(14,2), default=0)
    desconto_segurado=db.Column(db.Numeric(14,2), default=0)
    patronal=db.Column(db.Numeric(14,2), default=0)
    proventos=db.Column(db.Numeric(14,2), default=0)
    status=db.Column(db.String(40), default="Importada")

class Documento(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"), nullable=False)
    tipo=db.Column(db.String(80), nullable=False)
    nome_arquivo=db.Column(db.String(255))
    caminho=db.Column(db.String(500))
    hash_arquivo=db.Column(db.String(64))
    status=db.Column(db.String(40), default="Pendente")
    validade=db.Column(db.Date)
    observacoes=db.Column(db.Text)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class Atendimento(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"))
    data_hora=db.Column(db.DateTime, nullable=False)
    canal=db.Column(db.String(40), default="Presencial")
    local=db.Column(db.String(180))
    atendente=db.Column(db.String(120))
    status=db.Column(db.String(40), default="Agendado")
    observacoes=db.Column(db.Text)

class Pendencia(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"))
    tipo=db.Column(db.String(100), nullable=False)
    descricao=db.Column(db.Text, nullable=False)
    severidade=db.Column(db.String(20), default="Media")
    status=db.Column(db.String(30), default="Aberta")
    criado_em=db.Column(db.DateTime, default=datetime.utcnow)
    resolvido_em=db.Column(db.DateTime)
    responsavel=db.Column(db.String(120))

class Auditoria(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    usuario=db.Column(db.String(80))
    acao=db.Column(db.String(80), nullable=False)
    entidade=db.Column(db.String(80))
    entidade_id=db.Column(db.Integer)
    ip=db.Column(db.String(80))
    detalhes=db.Column(db.Text)
    criado_em=db.Column(db.DateTime, default=datetime.utcnow)

class Importacao(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    arquivo=db.Column(db.String(255))
    tipo=db.Column(db.String(80))
    registros_lidos=db.Column(db.Integer, default=0)
    registros_criados=db.Column(db.Integer, default=0)
    registros_atualizados=db.Column(db.Integer, default=0)
    registros_com_erro=db.Column(db.Integer, default=0)
    status=db.Column(db.String(30), default="Processando")
    criado_em=db.Column(db.DateTime, default=datetime.utcnow)
    mensagem=db.Column(db.Text)

class PendenciaImportada(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    origem_hash=db.Column(db.String(64), unique=True, nullable=False)
    matricula=db.Column(db.String(40), nullable=False)
    servidor=db.Column(db.String(180), nullable=False)
    pendencia=db.Column(db.Text, nullable=False)
    tipo_funcional=db.Column(db.String(60))
    observacao=db.Column(db.Text)
    recenseado_id=db.Column(db.Integer, db.ForeignKey("recenseado.id"))
    municipio=db.Column(db.String(120), default="Olho d'Água")
    codigo_ibge=db.Column(db.Integer, default=2510402)
    uf=db.Column(db.String(2), default="PB")
    status=db.Column(db.String(30), nullable=False, default="Pendente")
    criada_em=db.Column(db.DateTime, default=datetime.utcnow)

class PastaArquivo(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    nome=db.Column(db.String(180), nullable=False)
    pasta_pai_id=db.Column(db.Integer, db.ForeignKey("pasta_arquivo.id"))
    criada_em=db.Column(db.DateTime, default=datetime.utcnow)

class Arquivo(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    nome=db.Column(db.String(255), nullable=False)
    nome_armazenado=db.Column(db.String(80), unique=True, nullable=False)
    tamanho=db.Column(db.BigInteger, nullable=False)
    tipo_mime=db.Column(db.String(180))
    pasta_id=db.Column(db.Integer, db.ForeignKey("pasta_arquivo.id"))
    usuario=db.Column(db.String(80), nullable=False)
    criado_em=db.Column(db.DateTime, default=datetime.utcnow)
