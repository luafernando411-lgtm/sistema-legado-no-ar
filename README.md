# Censo Previdenciário — Flask + SQLAlchemy + Chart.js

Sistema de Censo Previdenciário com frontend servido pelo Flask, API autenticada e área privada de arquivos.

## O que já está implementado

### Cadastro e recenseamento
- CRUD de recenseados.
- CPF com validação de dígitos.
- Matrícula, cargo, entidade, vínculo, dados pessoais e contato.
- Fluxo `Em andamento` → `Concluído`.
- Data de conclusão e último acesso.
- Consulta paginada, busca e filtros.

### Grupo familiar e previdenciário
- Dependentes.
- Benefícios.
- Contribuições por competência.
- Atendimentos/agendamentos.

### Documentação e pendências
- Modelo de documento e status de conferência.
- Pendências por segurado.
- Severidade e responsável.
- Estrutura preparada para upload e hash de arquivos.

### Qualidade cadastral
- CPF ausente.
- Nome ausente.
- Duplicidade de CPF.
- Duplicidade de matrícula.
- Endpoint de validação.

### Importação e exportação
- Importação idempotente do relatório Excel de recenseados e da planilha de pendências funcionais.
- CPFs numéricos normalizados para 11 dígitos antes da validação.
- Pendências reconciliadas por matrícula e nome exatos; divergências ficam para revisão, sem criar ou associar segurados indevidamente.
- Atualização por matrícula/CPF.
- Exportação CSV.
- Registro das importações.

### Segurança e governança
- Login JWT em cookie `HttpOnly`, proteção CSRF e sessão de 30 minutos.
- Acesso limitado a `luizarrow3` e `josy`; senhas armazenadas com hash.
- Perfis `admin` e `operador`.
- Senhas com hash.
- Log de auditoria.
- CPF mascarado nas consultas de lista.
- Consultas e operações protegidas por autenticação.
- Pastas privadas e upload, download e exclusão de arquivos de até 6 GiB por arquivo.

### Dashboard
O frontend mantém a identidade visual do dashboard e consome `/api/dashboard` na mesma origem.

### Busca territorial e relatórios
- Busca de municípios no catálogo oficial de localidades do IBGE, incluindo código e UF.
- Indicadores de servidores agregados localmente por município, UF e tipo funcional; o IBGE não fornece dados individuais previdenciários.
- Relatório de conformidade agregado com status e campos ausentes, sem identificadores pessoais.
- As telas de pendências, busca IBGE e relatórios exigem autenticação.

## Instalação

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
cp env.example .env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48)); print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48)); print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"

python run.py
```

Abra `http://localhost:5000`.

Defina `CENSO_LUIZ_PASSWORD` e `CENSO_JOSY_PASSWORD` no arquivo `.env` com senhas fortes antes da primeira inicialização. As senhas ficam somente no ambiente local, não no código-fonte.

Opcionalmente, defina `CENSO_LUIZ_EMAIL` e `CENSO_JOSY_EMAIL` para permitir login pelos e-mails cadastrados. Sem essas variáveis, use os usuários `luizarrow3` e `josy`.

`CENSO_COMPLETED_PERCENT=50` inicializa a base com metade dos registros concluídos, de forma determinística, para a apresentação. Altere esse valor quando a base operacional passar a refletir conclusões reais.

O navegador exige login após cada atualização: a página inicial revoga a sessão anterior no servidor e os cookies são apagados. Em produção, sirva por HTTPS e mantenha `JWT_COOKIE_SECURE=true`.

## Execução com persistência

Para iniciar o backend e o PostgreSQL com volumes persistentes:

```bash
docker compose up --build -d
```

O Compose recusa iniciar se chaves e senhas não forem fornecidas no `.env`; não existem credenciais padrão no container.

Acesse `http://localhost:5000`. O banco e os arquivos ficam nos volumes `censo_database` e `censo_uploads`. O Gunicorn atende múltiplas requisições e usa timeout estendido para uploads grandes; ajuste `WEB_CONCURRENCY` e `GUNICORN_THREADS` conforme os recursos do servidor.

Em execução local, `python run.py` usa SQLite e a pasta `uploads/`. O limite de 6 GiB é por arquivo; o espaço total depende da capacidade do disco/volume.

## Deploy na Render

O arquivo `render.yaml` cria um web service Python com Gunicorn, um PostgreSQL e um disco persistente para os uploads. No painel da Render, use **New > Blueprint**, selecione o repositório e preencha `CENSO_LUIZ_PASSWORD` e `CENSO_JOSY_PASSWORD` quando solicitado. As chaves `SECRET_KEY` e `JWT_SECRET_KEY` são geradas pela Render.

O banco e os uploads persistem entre deploys. Para o disco persistente, selecione um plano compatível com disk; sem ele, arquivos enviados localmente no container podem ser perdidos durante um novo deploy.

## APIs principais

- `POST /api/auth/login`
- `GET /api/dashboard`
- `GET /api/pendencias`
- `GET /api/ibge/municipios?q=Olho%20d%27Agua`
- `GET /api/relatorios/conformidade`
- `GET /api/relatorios/executivo.pdf`
- `GET /brand/logo.svg`
- `GET /api/recenseados`
- `GET /api/recenseados/<id>`
- `POST /api/recenseados`
- `PUT /api/recenseados/<id>`
- `DELETE /api/recenseados/<id>`
- `POST /api/recenseados/<id>/concluir`
- `GET /api/recenseados/<id>/pendencias`
- `POST /api/recenseados/<id>/pendencias`
- `POST /api/recenseados/<id>/dependentes`
- `POST /api/recenseados/<id>/beneficios`
- `POST /api/recenseados/<id>/contribuicoes`
- `POST /api/recenseados/<id>/atendimentos`
- `POST /api/importacoes/recenseados`
- `GET /api/validacoes`
- `GET /api/relatorios/recenseados.csv`
- `GET /api/auditoria`
- `GET /api/metadados`
- `GET /health`

## Dados fornecidos

No primeiro início, o sistema importa uma vez o relatório de 126 recenseados e a planilha de 20 pendências funcionais. Três itens coincidem exatamente por nome e matrícula; os outros 17 permanecem na lista de revisão para conferência humana. A planilha identifica o município Olho d'Água, PB, código IBGE `2510402`.

Os valores agregados da folha que aparecem no dashboard são os valores já consolidados no painel anterior; para uma operação real, recomenda-se importar cada linha da folha para `Contribuicao` e cada benefício individual para `Beneficio`, em vez de trabalhar apenas com totais.

## Próximas extensões recomendadas

Para produção, acrescente:
1. armazenamento de documentos em S3/MinIO;
2. assinatura/aceite digital;
3. recuperação de senha e MFA;
4. fila Celery/RQ para grandes importações;
5. PostgreSQL;
6. LGPD: retenção, anonimização e trilhas de acesso mais granulares;
7. integração com folha/RH e eSocial, conforme os sistemas disponíveis;
8. motor de regras para pendências;
9. workflow de conferência em múltiplas etapas.
