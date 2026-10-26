"""Original local-only job ledger and conservative PDF region renderer.

Requires PyMuPDF >=1.24.4. No model calls, OCR calls, downloads or credentials.
Coordinates use unrotated PDF points. Complex pages use an external staged
candidate under the same fail-closed audit, rather than an unsafe native edit.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NUMBER = re.compile(r'(?<![A-Za-z0-9])[-−+]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][-+]?\d+)?%?')
CJK = re.compile(r'[\u3400-\u9fff]')
PLACEHOLDER = re.compile(r'ZXQ[A-Z]+\d+QXZ|\{\{(?:F|MATH)\d+\}\}|⟦[^⟧]+⟧')
SI_AFTER_NUMBER = re.compile(r'(?:\d|\.\d)\s*(μm|µm|nm|mm|cm|km|mW|kW|MHz|GHz|kHz|Hz|m/s|mL|kg|mg|°C|rad|sr|m|s|W|J|K)(?![A-Za-z])')

class Blocked(ValueError):
    pass

def pm():
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:
        raise Blocked('DEPENDENCY_MISSING: install PyMuPDF in a selected Python environment') from exc

def digest(value):
    if isinstance(value, Path):
        return hashlib.sha256(value.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,
                                     suffix='.part',delete=False) as f:
        f.write(data);temp=Path(f.name)
    temp.replace(path)

def require(condition, message):
    if not condition: raise Blocked(message)

def box(value):
    require(isinstance(value,list) and len(value)==4,'BBOX_INVALID')
    require(all(isinstance(n,(int,float)) and math.isfinite(n) for n in value),'BBOX_INVALID')
    require(value[0]<value[2] and value[1]<value[3],'BBOX_EMPTY')
    return pm().Rect(value)

def intersect(a,b):
    r=pm().Rect(a)&pm().Rect(b)
    return not r.is_empty and r.get_area()>0.01

def inside(a,b,tol=0.2):
    return a[0]>=b[0]-tol and a[1]>=b[1]-tol and a[2]<=b[2]+tol and a[3]<=b[3]+tol

def font_paths(config):
    result={}
    for kind,paths in config['fonts'].items():
        found=next((str(Path(p).resolve()) for p in paths if Path(p).is_file()),None)
        if found:result[kind]=found
    return result

def doctor():
    modules={}
    for name in ['PyMuPDF','babeldoc','paddleocr','mineru','pytesseract']:
        try:modules[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:modules[name]=None
    return {'python':sys.executable,'version':sys.version,'packages':modules,
            'commands':{x:shutil.which(x) for x in ['mineru','paddleocr','tesseract','pdftotext','xelatex','pdf2zh']},
            'fonts':font_paths(read(ROOT/'config/typography.json')),
            'network_used':False}

def prepare(source,job):
    source=Path(source).resolve();job=Path(job).resolve()
    require(source.is_file(),'SOURCE_MISSING')
    require(not job.exists(),'JOB_EXISTS: resume existing job or choose a fresh directory')
    with pm().open(source) as doc:
        require(not doc.needs_pass,'ENCRYPTED_PDF')
        require(len(doc)>0,'EMPTY_PDF')
        inventory={'version':1,'source_sha256':digest(source),'pages':[],'spans':[],'images':[]}
        units=[];protected=[]
        for pi,page in enumerate(doc,1):
            pg={'page':pi,'mediabox':list(page.mediabox),'cropbox':list(page.cropbox),
                'rotation':page.rotation,'rect':list(page.rect),'native_text_chars':len(page.get_text()),
                'vector_count':len(page.get_drawings())}
            inventory['pages'].append(pg)
            for bi,block in enumerate(page.get_text('dict',flags=pm().TEXTFLAGS_DICT & ~pm().TEXT_PRESERVE_IMAGES)['blocks']):
                if block['type']!=0:continue
                for li,line in enumerate(block['lines']):
                    for si,span in enumerate(line['spans']):
                        if not span['text'].strip():continue
                        sid=f'p{pi:04d}-b{bi:04d}-l{li:03d}-s{si:03d}'
                        rec={'id':sid,'page':pi,'bbox':span['bbox'],'text':span['text'],
                             'size_pt':span['size'],'font':span['font'],'flags':span['flags'],'color':span.get('color',0),
                             'direction':list(line['dir'])}
                        inventory['spans'].append(rec)
                        math_font=bool(re.search(r'Symbol|CMMI|CMSY|CMEX|Math',span['font'],re.I))
                        preserve=math_font or not re.search(r'[A-Za-z\u3400-\u9fff]',span['text'])
                        units.append({'id':sid,'page':pi,'bbox':list(span['bbox']),'span_ids':[sid],
                                      'source':span['text'],'action':'preserve' if preserve else 'translate',
                                      'role':'math' if math_font else 'unclassified','kind':'text',
                                      'size_pt':span['size'],'bold':bool(span['flags']&16),
                                      'italic':bool(span['flags']&2),'superscript':bool(span['flags']&1),'color':span.get('color',0),
                                      'reason':'math font' if math_font else 'numeric/symbol' if preserve else '',
                                      'protected_tokens':list(dict.fromkeys(SI_AFTER_NUMBER.findall(span['text'])))})
            for ii,image in enumerate(page.get_image_info(xrefs=True)):
                iid=f'p{pi:04d}-image-{ii:03d}'
                inventory['images'].append({'id':iid,'page':pi,'bbox':list(image['bbox']),
                                           'width':image['width'],'height':image['height'],'xref':image.get('xref',0)})
                protected.append({'id':iid,'page':pi,'bbox':list(image['bbox']),
                                  'kind':'figure','allow_label_edits':True})
        job.mkdir(parents=True)
        shutil.copyfile(source,job/'source.pdf')
        (job/'source-pages').mkdir()
        for pi,page in enumerate(doc,1):page.get_pixmap(matrix=pm().Matrix(2,2),alpha=False).save(job/'source-pages'/f'{pi:04d}.png')
    inventory['source_path']=str(source)
    write(job/'inventory.json',inventory)
    write(job/'plan.json',{'version':1,'source_sha256':inventory['source_sha256'],
                          'units':units,'protected':protected,'glossary':{},
                          'typography':read(ROOT/'config/typography.json'),
                          'source_inventory_reviewed':False,
                          'scope':'all visible paper content, including figure text and table cells',
                          'page_inventory':[{ 'page':p['page'],'all_visible_content_registered':False,
                                             'ocr_used':False,'notes':''} for p in inventory['pages']]})
    return {'job':str(job),'units':len(units),'pages':len(inventory['pages']),
            'next':'Inspect every source page; correct plan roles, OCR labels, cells and protected regions, then freeze'}

def validate_plan(plan,inv):
    require(plan['source_sha256']==inv['source_sha256'],'SOURCE_HASH_MISMATCH')
    require(plan.get('source_inventory_reviewed') is True,'SOURCE_INVENTORY_PENDING')
    expected_pages={p['page'] for p in inv['pages']}
    pages=plan.get('page_inventory',[])
    require(len(pages)==len(expected_pages) and {p['page'] for p in pages}==expected_pages,'PAGE_INVENTORY_MISSING')
    require(all(p.get('all_visible_content_registered') is True for p in pages),'VISIBLE_CONTENT_NOT_REGISTERED')
    require(all(isinstance(p.get('notes'),str) and p['notes'].strip() for p in pages),'PAGE_EVIDENCE_MISSING')
    spans={s['id']:s for s in inv['spans']};seen=[];ids=[];trans=[]
    rolekeys=set(plan['typography']['roles'])
    rects={p['page']:p['rect'] for p in inv['pages']}
    for u in plan['units']:
        require(isinstance(u.get('id'),str) and u['id'],'ID_REQUIRED');ids.append(u['id'])
        require(u['page'] in expected_pages,'PAGE_INVALID');box(u['bbox'])
        require(inside(u['bbox'],rects[u['page']]),'BBOX_OUTSIDE_PAGE')
        require(u.get('action') in ['translate','preserve'],'ACTION_INVALID')
        require(u.get('kind') in ['text','raster_label'],'KIND_INVALID')
        require(isinstance(u.get('source'),str) and u['source'].strip(),'SOURCE_TEXT_EMPTY')
        require(u.get('role') in rolekeys|{'math','code','unclassified'},'ROLE_INVALID')
        if u['action']=='translate':
            require(u['role'] in rolekeys,'ROLE_UNCLASSIFIED')
            require(isinstance(u.get('size_pt'),(int,float)) and 0<u['size_pt']<=144,'SOURCE_FONT_SIZE_REQUIRED')
            require(isinstance(u.get('bold'),bool) and isinstance(u.get('italic'),bool),'SOURCE_FONT_STYLE_REQUIRED')
            require(u.get('superscript',False) is False,'SUPERSCRIPT_REQUIRES_SPECIALIZED_LAYOUT')
            trans.append(u)
        else:require(bool(u.get('reason','').strip()),'PRESERVE_REASON_MISSING')
        known=[spans[x] for x in u.get('span_ids',[]) if x in spans]
        require(len(known)==len(u.get('span_ids',[])),'UNKNOWN_SPAN_ID')
        for s in known:
            require(s['page']==u['page'] and inside(s['bbox'],u['bbox']),'SOURCE_SPAN_REGION_MISMATCH')
        if known:
            require(''.join(s['text'] for s in known).replace('\n','')==u['source'].replace('\n',''),'SOURCE_TEXT_MISMATCH')
            if u['action']=='translate':
                require(all(abs(s['size_pt']-u['size_pt'])<0.01 and bool(s['flags']&16)==u['bold'] and bool(s['flags']&2)==u['italic'] for s in known),'MIXED_SOURCE_TYPOGRAPHY: split units by style')
        else:
            require(bool(u.get('source_visual_evidence','').strip()),'OCR_OR_IMAGE_SOURCE_EVIDENCE_MISSING')
        if u['action']=='translate' and u['role'] in ['table_cell','table_header']:
            require(isinstance(u.get('table_id'),str) and bool(u['table_id']),'TABLE_ID_MISSING')
            require(all(isinstance(u.get(k),int) and not isinstance(u[k],bool) and u[k]>=0 for k in ['row','col']),'TABLE_CELL_COORDINATES_MISSING')
            require(all(isinstance(u.get(k),int) and not isinstance(u[k],bool) and u[k]>=1 for k in ['rowspan','colspan']),'TABLE_MERGE_STRUCTURE_MISSING')
        seen.extend(u.get('span_ids',[]))
        if u['kind']=='raster_label' and u['action']=='translate':
            require(u.get('background_reviewed') is True,'RASTER_BACKGROUND_NOT_REVIEWED')
            rgb=u.get('background_rgb',[])
            require(len(rgb)==3 and all(isinstance(v,(int,float)) and 0<=v<=1 for v in rgb),'RASTER_BACKGROUND_INVALID')
    require(len(set(ids))==len(ids),'DUPLICATE_UNIT_ID')
    require(Counter(seen)==Counter(spans.keys()),'SOURCE_SPAN_COVERAGE: each original span must occur exactly once')
    require(bool(trans),'NO_TRANSLATION_UNITS')
    for i,u in enumerate(trans):
        for v in trans[i+1:]:
            require(u['page']!=v['page'] or not intersect(u['bbox'],v['bbox']),'TRANSLATION_REGIONS_OVERLAP')
        for v in plan['units']:
            if v['action']=='preserve' and v['page']==u['page']:
                require(not intersect(u['bbox'],v['bbox']),'PROTECTED_TEXT_OVERLAP')
        for v in plan.get('protected',[]):
            box(v['bbox']);require(v['page'] in expected_pages,'PROTECTION_PAGE_INVALID')
            if v['page']==u['page'] and intersect(u['bbox'],v['bbox']):
                require(v.get('kind')!='formula' and v.get('allow_label_edits') is True and u['role'] in ['figure_label','table_cell','table_header','table_note'] and inside(u['bbox'],v['bbox']),'PROTECTED_OBJECT_OVERLAP')
    # A native source page with no text must not be accepted as translated just
    # because the document contains a translatable unit on another page.
    for p in inv['pages']:
        if p['native_text_chars']<5:
            require(any(u['page']==p['page'] and u['action']=='translate' and not u.get('span_ids') for u in plan['units']),'SCAN_PAGE_UNREGISTERED')

def contract(job):
    job=Path(job);c=read(job/'contract.json')
    unhashed={k:v for k,v in c.items() if k!='contract_sha256'}
    require(digest(unhashed)==c['contract_sha256'],'CONTRACT_HASH_CHANGED')
    require(digest(job/'source.pdf')==c['source_sha256'],'FROZEN_SOURCE_CHANGED')
    require(digest(read(job/'plan.json'))==c['plan_sha256'],'PLAN_CHANGED_REFREEZE_REQUIRED')
    require(digest(read(job/'inventory.json'))==c['inventory_sha256'],'INVENTORY_CHANGED')
    require(digest(c['plan'])==c['plan_sha256'],'CONTRACT_PLAN_CHANGED')
    return c

def freeze(job):
    job=Path(job);p=read(job/'plan.json');inv=read(job/'inventory.json')
    require(digest(job/'source.pdf')==inv['source_sha256'],'SOURCE_CHANGED')
    validate_plan(p,inv)
    c={'version':1,'source_sha256':inv['source_sha256'],'plan_sha256':digest(p),
       'inventory_sha256':digest(inv),'plan':p}
    c['contract_sha256']=digest(c)
    write(job/'contract.json',c)
    return {'contract_sha256':c['contract_sha256'],'next':'batch -> translate -> submit -> review'}

def state(job):
    c=contract(job);t=read(Path(job)/'translations.json')
    require(t['contract_sha256']==c['contract_sha256'],'TRANSLATION_STALE')
    require(t['translation_sha256']==digest(t['translations']),'TRANSLATIONS_CHANGED_RESUBMIT_REQUIRED')
    return c,t

def batch(job):
    c=contract(job);p=c['plan']
    data={'contract_sha256':c['contract_sha256'],'glossary':p['glossary'],
          'instructions':'Current agent translates each occurrence; preserve numbers/tokens. Return id/text pairs.',
          'units':[{'id':u['id'],'page':u['page'],'role':u['role'],'source':u['source'],
                    'protected_tokens':u.get('protected_tokens',[]),'text':''}
                   for u in p['units'] if u['action']=='translate']}
    write(Path(job)/'batch.json',data);return {'path':str(Path(job)/'batch.json'),'units':len(data['units'])}

def validate_translations(c,rows):
    units={u['id']:u for u in c['plan']['units'] if u['action']=='translate'}
    require(isinstance(rows,list),'TRANSLATION_LIST_REQUIRED')
    require(Counter(r.get('id') for r in rows)==Counter(units.keys()),'TRANSLATION_IDS_MISSING_DUPLICATED_OR_UNKNOWN')
    for row in rows:
        u=units[row['id']];text=row.get('text')
        require(isinstance(text,str) and text.strip(),'TRANSLATION_EMPTY')
        require(not PLACEHOLDER.search(text),'UNRESOLVED_PLACEHOLDER')
        require(Counter(NUMBER.findall(u['source']))==Counter(NUMBER.findall(text)),f"NUMBER_CHANGED:{u['id']}")
        for token in u.get('protected_tokens',[]):
            require(u['source'].count(token)==text.count(token),f"PROTECTED_TOKEN_CHANGED:{u['id']}:{token}")
        for term,target in c['plan']['glossary'].items():
            if term in u['source']:
                require(target in text,f"GLOSSARY_VIOLATION:{u['id']}:{term}")
        if re.search('[A-Za-z]',u['source']) and not u.get('allow_unchanged',False):
            require(CJK.search(text),f"TARGET_LANGUAGE_MISSING:{u['id']}")

def submit(job,path):
    job=Path(job);c=contract(job);data=read(path)
    require(data.get('contract_sha256')==c['contract_sha256'],'BATCH_STALE')
    rows=data.get('translations',data.get('units'));validate_translations(c,rows)
    rows=[{'id':r['id'],'text':r['text']} for r in rows]
    write(job/'translations.json',{'contract_sha256':c['contract_sha256'],
                                   'translation_sha256':digest(rows),'translations':rows})
    return {'accepted':len(rows),'translation_sha256':digest(rows)}

def review_template(job):
    job=Path(job);c,t=state(job)
    require(not (job/'review.json').exists(),'REVIEW_EXISTS: preserve old evidence; update explicitly')
    review={'contract_sha256':c['contract_sha256'],'translation_sha256':t['translation_sha256'],
            'reviewer':'','method':'','extraction':{'status':'pending','pages':[],'evidence':''},
            'units':[{'id':r['id'],'meaning':'pending','terminology':'pending','numbers':'pending',
                      'structure':'pending','evidence':''} for r in t['translations']],
            'visual':{'candidate_sha256':'','status':'pending','pages':[],'evidence':''},
            'unresolved':[]}
    write(job/'review.json',review);return {'path':str(job/'review.json')}

def review_gate(job,c,t,visual_hash=None):
    r=read(Path(job)/'review.json');expected={p['page'] for p in read(Path(job)/'inventory.json')['pages']}
    require(r.get('contract_sha256')==c['contract_sha256'] and r.get('translation_sha256')==t['translation_sha256'],'REVIEW_STALE')
    require(bool(r.get('reviewer','').strip()) and bool(r.get('method','').strip()),'REVIEW_IDENTITY_MISSING')
    e=r.get('extraction',{});require(e.get('status')=='pass' and set(e.get('pages',[]))==expected and bool(e.get('evidence','').strip()),'EXTRACTION_REVIEW_PENDING')
    rows=r.get('units',[])
    require(Counter(x.get('id') for x in rows)==Counter(x['id'] for x in t['translations']),'UNIT_REVIEW_COVERAGE')
    require(all(all(x.get(k)=='pass' for k in ['meaning','terminology','numbers','structure']) and bool(x.get('evidence','').strip()) for x in rows),'SEMANTIC_REVIEW_PENDING')
    require(r.get('unresolved')==[],'UNRESOLVED_ISSUES')
    if visual_hash:
        v=r.get('visual',{})
        require(v.get('candidate_sha256')==visual_hash and v.get('status')=='pass' and set(v.get('pages',[]))==expected and bool(v.get('evidence','').strip()),'VISUAL_REVIEW_PENDING_OR_STALE')
    return r

def source_style(u,p):
    r=p['typography']['roles'][u['role']]
    size=u['size_pt'] if r['size_pt']=='source' else r['size_pt']
    bold=u['bold'] if r['weight']=='source' else r['weight']=='bold'
    italic=u['italic'] if r.get('italic','source')=='source' else bool(r['italic'])
    return float(size),bold,italic

def render(job):
    job=Path(job);c,t=state(job);review_gate(job,c,t);p=c['plan']
    inv=read(job/'inventory.json')
    require(all(x['rotation']==0 and abs(x['cropbox'][0])<0.01 and abs(x['cropbox'][1])<0.01 for x in inv['pages']),'NATIVE_COORDINATES_UNSUPPORTED: use audited external engine')
    fonts=font_paths(p['typography']);require('regular' in fonts and 'bold' in fonts,'CJK_FONT_MISSING')
    rows={r['id']:r['text'] for r in t['translations']}
    source=pm().open(job/'source.pdf');draft=pm().open(stream=source.tobytes(),filetype='pdf')
    fit_report=[]
    try:
        for u in p['units']:
            if u['action']!='translate':continue
            pi=u['page']-1;page=draft[pi];rect=box(u['bbox'])
            size,bold,italic=source_style(u,p)
            # Slanted/rotated text and superscripts need specialized typography.
            # Native rejects them rather than silently dropping their source style.
            require(not italic,'NATIVE_ITALIC_REQUIRES_SPECIALIZED_LAYOUT')
            require(all(s.get('direction',[1,0])==[1.0,0.0] for s in inv['spans'] if s['id'] in u.get('span_ids',[])),'NATIVE_ROTATED_TEXT_UNSUPPORTED')
            fontfile=fonts['bold' if bold else 'regular'];fname='PTFBold' if bold else 'PTFRegular'
            value=u.get('color',0);color=tuple(((value>>shift)&255)/255 for shift in (16,8,0))
            page.insert_font(fontname=fname,fontfile=fontfile)
            # Fitting uses a disposable page; there is no iterative font shrink.
            probe=pm().open();pp=probe.new_page(width=page.rect.width,height=page.rect.height)
            pp.insert_font(fontname=fname,fontfile=fontfile)
            spare=pp.insert_textbox(rect,rows[u['id']],fontname=fname,fontsize=size,lineheight=1.1)
            probe.close()
            require(spare>=-0.01,f"FONT_LAYOUT_CONFLICT:{u['id']}:{spare:.3f}")
            if u['kind']=='text':
                page.add_redact_annot(rect,fill=False,cross_out=False)
                page.apply_redactions(images=0,graphics=0,text=0)
            else:
                page.draw_rect(rect,color=None,fill=tuple(u['background_rgb']),overlay=True)
            spare=page.insert_textbox(rect,rows[u['id']],fontname=fname,fontfile=fontfile,fontsize=size,lineheight=1.1,color=color)
            require(spare>=-0.01,'RENDER_FIT_INCONSISTENT')
            fit_report.append({'id':u['id'],'page':u['page'],'size_pt':size,'bold':bold,
                               'fontfile':fontfile,'font_sha256':digest(Path(fontfile)),'spare_height_pt':spare})
        path=job/'candidate.pdf';temp=job/'native-candidate.part.pdf'
        draft.save(temp,garbage=4,deflate=True)
        temp.replace(path)
        write(job/'candidate.json',{'engine':'native-conservative','candidate_sha256':digest(path),
                                   'contract_sha256':c['contract_sha256'],'translation_sha256':t['translation_sha256'],
                                   'font_report':fit_report})
        return {'candidate':str(path),'sha256':digest(path),'next':'audit, inspect actual pages, attest visual review, audit again'}
    finally:draft.close();source.close()

def stage(job,path):
    job=Path(job);path=Path(path).resolve();c,t=state(job);review_gate(job,c,t)
    require(path!= (job/'source.pdf').resolve(),'CANNOT_STAGE_SOURCE')
    require(path!= (job/'candidate.pdf').resolve(),'ALREADY_CANDIDATE')
    require(digest(path)!=c['source_sha256'],'UNTRANSLATED_SOURCE_CANDIDATE')
    with pm().open(path) as d:require(not d.needs_pass and len(d)>0,'CANDIDATE_INVALID')
    temp=job/'external-candidate.part.pdf';shutil.copyfile(path,temp);temp.replace(job/'candidate.pdf')
    write(job/'candidate.json',{'engine':'external-staged','candidate_sha256':digest(job/'candidate.pdf'),
                               'contract_sha256':c['contract_sha256'],'translation_sha256':t['translation_sha256'],
                               'font_report':[]})
    return {'candidate_sha256':digest(job/'candidate.pdf')}

def normalize(s):
    return re.sub(r'\s+','',s)

def font_family(name):
    name=re.sub(r'^[A-Z]{6}\+','',name)
    name=re.sub(r'[\s,-]*Regular$','',name,flags=re.I)
    return re.sub(r'[\s,-]','',name).casefold()

def pixel_checks(src,dst,masks,protected,scale=2):
    a=src.get_pixmap(matrix=pm().Matrix(scale,scale),alpha=False)
    b=dst.get_pixmap(matrix=pm().Matrix(scale,scale),alpha=False)
    require((a.width,a.height,a.n)==(b.width,b.height,b.n),'RASTER_GEOMETRY_CHANGED')
    # Pillow is deliberately optional; bytes and row-wise masks suffice.
    aa=a.samples;bb=b.samples;w=a.width;h=a.height;n=a.n
    regions=[]
    for r in masks:
        regions.append((max(0,math.floor(r[0]*scale)-1),max(0,math.floor(r[1]*scale)-1),
                        min(w,math.ceil(r[2]*scale)+1),min(h,math.ceil(r[3]*scale)+1)))
    changed=0;outside=0
    protected_fail=[]
    for y in range(h):
        cuts=sorted((r[0],r[2]) for r in regions if r[1]<=y<r[3])
        end=0;segments=[]
        for l,r in cuts:
            if l>end:segments.append((end,l))
            end=max(end,r)
        if end<w:segments.append((end,w))
        for l,r in segments:
            x0=(y*w+l)*n;x1=(y*w+r)*n;outside+=r-l
            if aa[x0:x1]!=bb[x0:x1]:
                for x in range(l,r):
                    k=(y*w+x)*n
                    if max(abs(aa[k+i]-bb[k+i]) for i in range(n))>8:changed+=1
    for pr in protected:
        r=pr['bbox'];x0=max(0,math.floor(r[0]*scale));x1=min(w,math.ceil(r[2]*scale));y0=max(0,math.floor(r[1]*scale));y1=min(h,math.ceil(r[3]*scale))
        diffs=0
        for y in range(y0,y1):
            for x in range(x0,x1):
                if pr.get('kind')!='formula' and pr.get('allow_label_edits') and any(l<=x<rr and top<=y<bottom for l,top,rr,bottom in regions):continue
                k=(y*w+x)*n
                if max(abs(aa[k+i]-bb[k+i]) for i in range(n))>8:diffs+=1
        if diffs:protected_fail.append({'id':pr['id'],'changed_pixels':diffs})
    return {'outside_changed_pixels':changed,'outside_checked_pixels':outside,'protected_changes':protected_fail}

def audit(job):
    job=Path(job);c,t=state(job);p=c['plan'];inv=read(job/'inventory.json')
    path=job/'candidate.pdf';meta=read(job/'candidate.json');issues=[];pages=[]
    def issue(code,**more):issues.append({'code':code,**more})
    require(path.is_file(),'NO_CANDIDATE')
    actual=digest(path)
    if meta.get('candidate_sha256')!=actual:issue('CANDIDATE_HASH_CHANGED')
    if meta.get('contract_sha256')!=c['contract_sha256'] or meta.get('translation_sha256')!=t['translation_sha256']:issue('CANDIDATE_STALE')
    targets={r['id']:r['text'] for r in t['translations']};(job/'qa-pages').mkdir(exist_ok=True)
    src=pm().open(job/'source.pdf');dst=pm().open(path)
    try:
        if len(src)!=len(dst):issue('PAGE_COUNT_CHANGED',source=len(src),candidate=len(dst))
        for pi in range(min(len(src),len(dst))):
            sp=src[pi];dp=dst[pi];page_no=pi+1
            if list(sp.mediabox)!=list(dp.mediabox) or list(sp.cropbox)!=list(dp.cropbox) or sp.rotation!=dp.rotation:issue('PAGE_GEOMETRY_CHANGED',page=page_no);continue
            uu=[u for u in p['units'] if u['page']==page_no and u['action']=='translate']
            protections=deepcopy([x for x in p.get('protected',[]) if x['page']==page_no])
            protections.extend({'id':u['id'],'bbox':u['bbox'],'allow_label_edits':False} for u in p['units'] if u['page']==page_no and u['action']=='preserve')
            pix=pixel_checks(sp,dp,[u['bbox'] for u in uu],protections)
            if pix['outside_changed_pixels']:issue('UNEXPECTED_GRAPHICS_OR_TEXT_CHANGE',page=page_no,pixels=pix['outside_changed_pixels'])
            if pix['protected_changes']:issue('PROTECTED_REGION_CHANGED',page=page_no,regions=pix['protected_changes'])
            text=dp.get_text()
            if PLACEHOLDER.search(text):issue('PLACEHOLDER_RESIDUE',page=page_no)
            allsp=[s for b in dp.get_text('dict',flags=pm().TEXTFLAGS_DICT & ~pm().TEXT_PRESERVE_IMAGES)['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
            for u in uu:
                r=box(u['bbox']);clip=r+(-0.3,-0.3,0.3,0.3)
                got=dp.get_text(clip=clip)
                if normalize(targets[u['id']]) not in normalize(got):issue('TARGET_TEXT_NOT_IN_SOURCE_REGION',page=page_no,id=u['id'])
                if u['kind']=='text' and re.search('[A-Za-z]',u['source']) and normalize(u['source']) in normalize(got) and normalize(u['source']) not in normalize(targets[u['id']]):issue('SOURCE_TEXT_RESIDUE',page=page_no,id=u['id'])
                size,bold,italic=source_style(u,p)
                fontfile=font_paths(p['typography']).get('bold' if bold else 'regular')
                expected_font=pm().Font(fontfile=fontfile).name if fontfile else None
                relevant=[s for s in allsp if intersect(s['bbox'],u['bbox']) and CJK.search(s['text'])]
                if not relevant:issue('CJK_TEXT_LAYER_MISSING',page=page_no,id=u['id'])
                for s in relevant:
                    if s.get('color',0)!=u.get('color',0):issue('TEXT_COLOR_CHANGED',page=page_no,id=u['id'])
                    if not expected_font or font_family(s['font'])!=font_family(expected_font):issue('FONT_FAMILY_CHANGED',page=page_no,id=u['id'],expected=expected_font,actual=s['font'])
                    if abs(s['size']-size)>0.08:issue('FONT_SIZE_CHANGED',page=page_no,id=u['id'],expected=size,actual=s['size'])
                    font_bold=bool(s['flags']&16) or bool(re.search(r'Bold|Black',s['font'],re.I))
                    if font_bold!=bold:issue('FONT_WEIGHT_CHANGED',page=page_no,id=u['id'],font=s['font'])
                    if bool(s['flags']&2)!=italic:issue('FONT_ITALIC_CHANGED',page=page_no,id=u['id'])
                    if not inside(s['bbox'],clip,tol=0.5):issue('TEXT_OVERFLOW',page=page_no,id=u['id'])
            dp.get_pixmap(matrix=pm().Matrix(2,2),alpha=False).save(job/'qa-pages'/f'{page_no:04d}.png')
            pair=pm().open();pp=pair.new_page(width=sp.rect.width*2,height=sp.rect.height)
            pp.show_pdf_page(pm().Rect(0,0,sp.rect.width,sp.rect.height),src,pi)
            pp.show_pdf_page(pm().Rect(sp.rect.width,0,sp.rect.width*2,sp.rect.height),dst,pi)
            pp.get_pixmap(matrix=pm().Matrix(1.25,1.25),alpha=False).save(job/'qa-pages'/f'{page_no:04d}-pair.png');pair.close()
            pages.append({'page':page_no,**pix})
    finally:src.close();dst.close()
    try:r=review_gate(job,c,t,actual)
    except (Blocked,FileNotFoundError) as exc:issue('REVIEW_GATE',detail=str(exc));r=None
    report={'status':'pass' if not issues else 'fail','candidate_sha256':actual,
            'contract_sha256':c['contract_sha256'],'translation_sha256':t['translation_sha256'],
            'pages':pages,'issues':issues,'review_sha256':digest(r) if r else None,
            'limits':['No automated proof of semantic truth or honest review; human/agent attestations required',
                      'Pixel comparisons inside declared editable regions require visual review'],
            'engine':meta['engine']}
    write(job/'audit.json',report)
    return report

def publish(job,out):
    job=Path(job);out=Path(out).resolve();report=audit(job)
    require(report['status']=='pass','DELIVERY_GATE_FAILED: inspect audit.json')
    require(not out.exists(),'OUTPUT_EXISTS: never overwrite an existing deliverable')
    out.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation protects against a last-moment overwrite race.
    with out.open('xb') as f:f.write((job/'candidate.pdf').read_bytes())
    write(job/'delivery.json',{'output':str(out),'sha256':digest(out),'audit_sha256':digest(report),
                               'status':'released-with-recorded-review'})
    return {'output':str(out),'sha256':digest(out)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    s=sub.add_parser('prepare');s.add_argument('source');s.add_argument('job')
    for name in ['freeze','batch','review-template','render','audit']:
        s=sub.add_parser(name);s.add_argument('job')
    for name in ['submit','stage','publish']:
        s=sub.add_parser(name);s.add_argument('job');s.add_argument('path')
    args=parser.parse_args()
    try:
        funcs={'doctor':lambda:doctor(),'prepare':lambda:prepare(args.source,args.job),
               'freeze':lambda:freeze(args.job),'batch':lambda:batch(args.job),
               'submit':lambda:submit(args.job,args.path),'review-template':lambda:review_template(args.job),
               'render':lambda:render(args.job),'stage':lambda:stage(args.job,args.path),
               'audit':lambda:audit(args.job),'publish':lambda:publish(args.job,args.path)}
        result=funcs[args.command]();print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result,dict) and result.get('status')=='fail' else 0
    except (Blocked,FileNotFoundError,KeyError,TypeError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
