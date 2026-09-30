import re
from datetime import datetime, date

def digits(v):
    return re.sub(r"\D","",str(v or ""))

def normalize_cpf(v):
    value=digits(v)
    return value.zfill(11) if value and len(value)<11 else value

def mask_cpf(cpf):
    s=digits(cpf)
    return f"***.***.***-{s[-2:]}" if len(s)>=2 else "***"

def valid_cpf(cpf):
    s=digits(cpf)
    if len(s)!=11 or s==s[0]*11: return False
    for pos in (9,10):
        total=sum(int(s[i])*(pos+1-i) for i in range(pos))
        d=(total*10)%11
        if d==10:d=0
        if d!=int(s[pos]):return False
    return True

def parse_date(v):
    if not v: return None
    if isinstance(v,date): return v
    for f in ("%d/%m/%Y","%Y-%m-%d"):
        try:return datetime.strptime(str(v)[:10],f).date()
        except ValueError: pass
    return None

def parse_datetime(v):
    if not v:return None
    if isinstance(v,datetime):return v
    for f in ("%d/%m/%Y %H:%M","%d/%m/%Y","%Y-%m-%dT%H:%M:%S"):
        try:return datetime.strptime(str(v)[:19],f)
        except ValueError: pass
    return None

def iso(v):
    return v.isoformat() if v else None

def money(v):
    return float(v or 0)
