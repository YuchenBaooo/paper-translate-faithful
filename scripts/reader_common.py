"""Shared, local-only utilities for the reader edition (not the native contract)."""
import hashlib,json,math,re
from pathlib import Path
import pymupdf as fitz

class InvalidPlan(ValueError):pass
def require(ok,message):
    if not ok:raise InvalidPlan(message)
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def rect(value):
    require(isinstance(value,(list,tuple)) and len(value)==4,'RECT_REQUIRED')
    require(all(isinstance(v,(int,float)) and math.isfinite(v) for v in value),'NONFINITE_RECT')
    r=fitz.Rect(value);require(r.width>0 and r.height>0,'EMPTY_RECT');return r
def resolve(base,path):return (Path(base)/path).resolve()
def family(s):return re.sub(r'^[A-Z]{6}\+','',s)
def cjk(ch):return bool(re.match(r'[\u2000-\u206f\u2e80-\u9fff\uf900-\ufaff\uff00-\uffff]',ch))
def font_path(name):
    candidates={'auto-cjk':['C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'],
                'auto-cjk-bold':['C:/Windows/Fonts/msyhbd.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']}
    if name in candidates:
        p=next((p for p in candidates[name] if Path(p).is_file()),None)
        require(p is not None,'CJK_FONT_MISSING: configure a local font path');return p
    require(Path(name).is_file(),'FONT_MISSING:'+str(name));return str(Path(name).resolve())
def sources(spec,base):
    docs={};manifest={}
    for key,s in spec['sources'].items():
        p=resolve(base,s['path']);require(p.is_file(),'SOURCE_MISSING:'+str(p))
        require(sha(p)==s['sha256'],'SOURCE_HASH_CHANGED:'+key)
        docs[key]=fitz.open(p);manifest[key]={'path':str(p),'sha256':sha(p)}
    return docs,manifest
