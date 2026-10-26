"""Explicit reader-plan renderer. Translation, object extraction and review stay separate."""
import argparse,collections,json,math,re,shutil
from pathlib import Path
from reader_common import *

ASSET=re.compile(r'\{\{asset:([A-Za-z0-9_.-]+)\}\}')
OPEN='（【《“「([';CLOSE='，。；：！？、）】》”」,.;:!?)]'

def tokens(text):
    return re.findall(r'\[\d+(?:[\d,\s–-]*\d+)?\]|[A-Za-z0-9][A-Za-z0-9.\-/]*|\s+|.',text.replace('\n',''))

def wrap(atoms,width,indent,size,leading=1.43):
    """Atomic equations, whole Latin words and CJK closing-mark runs."""
    require(width>0 and 0<=indent<width,'FRAME_WIDTH')
    for a in atoms:require(a['width']<=width+.01,'ATOM_WIDER_THAN_COLUMN:'+a.get('asset',a.get('text','')))
    rows=[];line=[];used=indent;ind=indent
    for atom in atoms:
        if not line and atom.get('text')==' ':continue
        if line and used+atom['width']>width+.01:
            carry=[];ch=atom.get('text')
            if ch and ch in CLOSE:
                while line and line[-1].get('text') and line[-1]['text'] in CLOSE:carry.insert(0,line.pop())
                if line:carry.insert(0,line.pop())
                while line and line[-1].get('text') and line[-1]['text'] in OPEN:carry.insert(0,line.pop())
            elif line[-1].get('text') and line[-1]['text'] in OPEN:carry=[line.pop()]
            if line:rows.append((line,ind))
            line=carry;ind=0;used=sum(x['width'] for x in line)
            # No infinite empty-line loop and no silently oversized equation.
            require(used+atom['width']<=width+.01,'BOUND_PUNCTUATION_WIDER_THAN_COLUMN')
        elif not line and used+atom['width']>width+.01:ind=0;used=0
        line.append(atom);used+=atom['width']
    if line:rows.append((line,ind))
    result=[]
    for aa,ind in rows:
        while aa and aa[-1].get('text')==' ':aa.pop()
        if not aa:continue
        asc=max([size*.86]+[a['ascent'] for a in aa]);desc=max([size*.22]+[a['descent'] for a in aa])
        result.append({'atoms':aa,'indent':ind,'ascent':asc,'descent':desc,'height':max(size*leading,asc+desc+1.7)})
    return result

def refs_in_block(b):
    refs=[]
    if b.get('asset'):refs.append(b['asset'])
    for key in ['text','caption']:refs.extend(ASSET.findall(b.get(key,'')))
    if b['kind']=='references':refs.extend(e['asset'] for e in b['entries'])
    return refs

def validate(plan,manifest):
    require(plan.get('edition')=='reader','READER_EDITION_REQUIRED')
    require(plan.get('mode_basis','').strip(),'USER_PRIORITY_BASIS_REQUIRED')
    unit_ids=[u['id'] for u in plan['units']];require(len(unit_ids)==len(set(unit_ids)),'DUPLICATE_UNIT_ID')
    asset_ids=[a['id'] for a in manifest['assets']];require(len(asset_ids)==len(set(asset_ids)),'DUPLICATE_ASSET_ID')
    units={u['id']:u for u in plan['units']};am={a['id']:a for a in manifest['assets']}
    blocks=plan['blocks'];require(len({b['id'] for b in blocks})==len(blocks),'DUPLICATE_BLOCK_ID')
    claimed=[];actual=collections.Counter()
    for b in blocks:
        require(b['kind'] in ['heading','paragraph','equation','figure','table','references','page_break'],'BLOCK_KIND')
        require(all(uid in units for uid in b.get('unit_ids',[])),'UNKNOWN_UNIT')
        claimed.extend(b.get('unit_ids',[]))
        text_ids=b.get('text_unit_ids',[uid for uid in b.get('unit_ids',[]) if units[uid].get('delivery')=='text'])
        require(all(uid in b.get('unit_ids',[]) and units[uid].get('delivery')=='text' for uid in text_ids),'TEXT_UNIT_BINDING')
        expected_text=''.join(units[uid].get('target','') for uid in text_ids)
        field='caption' if b['kind'] in ['figure','table'] else 'text'
        require(re.sub(r'\s+','',b.get(field,''))==re.sub(r'\s+','',expected_text),'TARGET_TEXT_CHANGED_OR_MISSING:'+b['id'])
        require(not (b['kind']=='heading' and ASSET.search(b.get('text',''))),'HEADING_MATH_USE_STYLED_PARAGRAPH')
        actual.update(refs_in_block(b))
        require(all(a in asset_ids for a in refs_in_block(b)),'UNKNOWN_ASSET:'+b['id'])
        for uid in b.get('unit_ids',[]):
            if units[uid].get('delivery')=='asset':require(units[uid].get('asset') in refs_in_block(b),'ASSET_UNIT_NOT_PLACED:'+uid)
        for key in ['text','caption']:
            require('{{' not in ASSET.sub('',b.get(key,'')),'MALFORMED_ASSET_TOKEN')
        if b['kind']=='references':
            require(len({e['id'] for e in b['entries']})==len(b['entries']),'DUPLICATE_REFERENCE')
        if b['kind'] in ['figure','table','equation']:
            require(b.get('asset') in asset_ids,'OBJECT_ASSET_REQUIRED')
            require(am[b['asset']]['role']==b['kind'],'OBJECT_ASSET_ROLE')
        if b['kind']=='references':require(all(am[e['asset']]['role']=='reference' for e in b['entries']),'REFERENCE_ASSET_ROLE')
    require(collections.Counter(claimed)==collections.Counter(unit_ids),'UNIT_COVERAGE_EXACTLY_ONCE')
    expected=collections.Counter(a for u in plan['units'] for a in u.get('required_assets',[]))
    require(actual==expected,'ASSET_OCCURRENCES_MISSING_OR_DUPLICATED')
    for u in plan['units']:
        require(u.get('action') in ['translate','preserve'],'UNIT_ACTION')
        require(u.get('delivery') in ['text','asset'],'UNIT_DELIVERY_REQUIRED')
        require(bool(u.get('source','').strip()),'SOURCE_EVIDENCE_REQUIRED')
        if u['action']=='translate':require(bool(u.get('target','').strip()),'TRANSLATION_TARGET_REQUIRED')
        for aid in u.get('required_assets',[]):require(aid in am and u['id'] in am[aid].get('source_units',[]),'ASSET_SOURCE_UNIT_BINDING')
        if u['action']=='preserve':require(bool(u.get('reason','').strip()),'PRESERVE_REASON')
        if u['delivery']=='text':require(bool(u.get('target','').strip()),'TARGET_REQUIRED')
        else:
            aid=u.get('asset');require(aid in am and u['id'] in am[aid].get('source_units',[]),'ASSET_UNIT_BINDING')
    require(bool(plan['styles']) and 'body' in plan['styles'],'STYLES_REQUIRED')
    page=plan['page']
    require(all(isinstance(x,(int,float)) and math.isfinite(x) and x>0 for x in [page['width'],page['height']]),'PAGE_DIMENSIONS')
    require(len(page['margins'])==4 and all(isinstance(x,(int,float)) and math.isfinite(x) and x>=0 for x in page['margins']),'PAGE_MARGINS')
    for s in plan['styles'].values():
        require(isinstance(s['size_pt'],(int,float)) and math.isfinite(s['size_pt']) and 1<=s['size_pt']<=144,'STYLE_SIZE')
        require(isinstance(s.get('bold',False),bool),'STYLE_WEIGHT')
    return True

class Reader:
    def __init__(self,plan,manifest,asset_pdf):
        self.p=plan;self.asset_pdf=asset_pdf;self.assets={a['id']:a for a in manifest['assets']}
        cfg=plan['page'];self.W=cfg['width'];self.H=cfg['height'];m=cfg['margins']
        self.left,self.top,self.right,self.bottom=m;self.columns=cfg.get('columns',2);gap=cfg.get('gutter',20)
        require(self.columns in [1,2] and gap>=0,'COLUMNS_OR_GUTTER')
        self.width=(self.W-self.left-self.right-gap*(self.columns-1))/self.columns;self.gap=gap
        self.limit=self.H-self.bottom;require(self.width>20 and self.limit-self.top>40,'EMPTY_PAGE_FRAME')
        fonts=plan['fonts'];self.fontpaths={False:font_path(fonts['regular']),True:font_path(fonts['bold'])}
        self.fonts={k:fitz.Font(fontfile=v) for k,v in self.fontpaths.items()}
        self.latin_names={False:fonts.get('latin_regular','tiro'),True:fonts.get('latin_bold','tibo')}
        self.latin={k:fitz.Font(v) for k,v in self.latin_names.items()}
        self.doc=fitz.open();self.locations=[];self.objects=[];self.text_segments=[];self.toc=[]
        self.pending=[];self.wide=[];self.newpage()
    def style(self,key):return self.p['styles'][key]
    def atoms(self,text,key):
        s=self.style(key);size=s['size_pt'];bold=s.get('bold',False);out=[]
        for chunk in re.split(r'(\{\{asset:[A-Za-z0-9_.-]+\}\})',text):
            match=ASSET.fullmatch(chunk)
            if match:
                a=self.assets[match[1]];base=a['baseline_pt'];space=s.get('math_space_pt',.5)
                require(a['role']=='inline_math','NONINLINE_ASSET_IN_SENTENCE')
                out.append({'asset':a['id'],'width':a['width']+2*space,'ascent':base,'descent':a['height']-base,'space':space})
            else:
                for tx in tokens(chunk):
                    if tx.isspace():tx=' '
                    require(all((self.fonts[bold] if cjk(ch) else self.latin[bold]).has_glyph(ord(ch)) for ch in tx),'FONT_GLYPH_MISSING:'+tx)
                    width=sum((self.fonts[bold] if cjk(ch) else self.latin[bold]).text_length(ch,fontsize=size) for ch in tx)
                    out.append({'text':tx,'width':width,'ascent':size*.86,'descent':size*.22,'style':key})
        return out
    @property
    def x(self):return self.left+self.col*(self.width+self.gap)
    def text(self,text,x,y,key,align='left'):
        s=self.style(key);size=s['size_pt'];bold=s.get('bold',False);color=tuple(s.get('color',[.13,.12,.125]))
        width=sum((self.fonts[bold] if cjk(ch) else self.latin[bold]).text_length(ch,fontsize=size) for ch in text)
        if align=='center':x-=width/2
        for run in re.findall(r'[\u2000-\u206f\u2e80-\u9fff\uf900-\ufaff\uff00-\uffff]+|[^\u2000-\u206f\u2e80-\u9fff\uf900-\ufaff\uff00-\uffff]+',text):
            cn=cjk(run[0]);name=('ZHB' if bold else 'ZHR') if cn else self.latin_names[bold]
            self.pg.insert_text((x,y),run,fontname=name,fontfile=self.fontpaths[bold] if cn else None,fontsize=size,color=color)
            self.text_segments.append({'text':run,'point':[x,y],'size_pt':size,'page':len(self.doc),'font':name})
            x+=sum((self.fonts[bold] if cjk(ch) else self.latin[bold]).text_length(ch,fontsize=size) for ch in run)
    def newpage(self):
        self.pg=self.doc.new_page(width=self.W,height=self.H);self.col=0;self.frame_top=self.top;self.y=self.top;self.column_content=False
        header=self.p.get('running_header','')
        if header:self.pg.insert_text((self.left,max(12,self.top-20)),header,fontname='ZHR',fontfile=self.fontpaths[False],fontsize=7,color=(.45,.45,.45))
        self.pg.insert_text((self.W/2-3,self.H-15),str(len(self.doc)),fontsize=8,fontname='tiro',color=(.45,.45,.45))
        if self.wide:
            self.draw_float(self.wide.pop(0),full=True);self.frame_top=self.y+8;self.y=self.frame_top
        self.flush_float()
    def advance(self):
        if self.col+1<self.columns:self.col+=1;self.y=self.frame_top;self.column_content=False;self.flush_float()
        else:self.newpage()
    def ensure(self,height):
        require(height<=self.limit-self.top+.01,'BLOCK_TALLER_THAN_PAGE')
        loops=0
        while self.y+height>self.limit+.01:
            self.advance();loops+=1;require(loops<4+len(self.pending)+len(self.wide)+len(self.p['blocks']),'LAYOUT_NO_PROGRESS')
    def line(self,line,width,key,block_id,justify=False,full=False):
        x=self.left if full else self.x;baseline=self.y+line['ascent'];xx=x+line['indent']
        aa=line['atoms'];total=sum(a['width'] for a in aa)
        stretch=min(self.style(key).get('max_tracking_pt',.6),max(0,(width-line['indent']-total)/max(1,len(aa)-1))) if justify else 0
        for a in aa:
            if 'asset' in a:
                obj=self.assets[a['asset']];dest=fitz.Rect(xx+a['space'],baseline-obj['baseline_pt'],xx+a['space']+obj['width'],baseline-obj['baseline_pt']+obj['height'])
                self.pg.show_pdf_page(dest,self.asset_pdf,obj['page']-1,keep_proportion=False)
                self.objects.append({'kind':'inline_math','id':block_id,'asset':obj['id'],'page':len(self.doc),'bbox':list(dest)})
            else:self.text(a['text'],xx,baseline,a['style'])
            xx+=a['width']+stretch
        self.locations.append({'kind':'line','id':block_id,'page':len(self.doc),'bbox':[x,self.y,x+width,self.y+line['height']],
            'text':''.join(a.get('text','') for a in aa),'style':key,'has_math':any('asset' in a for a in aa)})
        self.y+=line['height']
    def paragraph(self,b,after=0):
        key=b.get('style','body');s=self.style(key);full=b.get('full_width',False)
        if full and self.column_content:self.newpage()
        width=self.W-self.left-self.right if full else self.width
        lines=wrap(self.atoms(b['text'],key),width,s.get('indent_em',2)*s['size_pt'],s['size_pt'],s.get('leading',1.43))
        if b.get('keep_together'):self.ensure(sum(l['height'] for l in lines)+s.get('after_pt',5)+after)
        elif len(lines)>1:self.ensure(sum(l['height'] for l in lines[:2]))
        for j,line in enumerate(lines):
            if j==max(0,len(lines)-2):self.ensure(sum(l['height'] for l in lines[j:])+s.get('after_pt',5)+after)
            self.ensure(line['height']);self.line(line,width,key,b['id'],j<len(lines)-1 and s.get('justify',True),full)
        self.y+=s.get('after_pt',5)
        if full:self.frame_top=self.y;self.col=0
        else:self.column_content=True
    def heading(self,b,following=None):
        key=b.get('style','heading');s=self.style(key);full=b.get('full_width',False)
        if full and self.column_content:self.newpage()
        reserve=s['size_pt']*2.5+self.style('body')['size_pt']*3
        if following and following['kind']=='heading':reserve+=self.style(following.get('style','heading'))['size_pt']*2.5
        self.ensure(reserve);self.y+=s.get('before_pt',6)
        width=self.W-self.left-self.right if full else self.width;x=self.left if full else self.x
        self.text(b['text'],x+width/2 if s.get('align')=='center' else x,self.y+s['size_pt']*.88,key,s.get('align','left'))
        self.locations.append({'kind':'heading','id':b['id'],'page':len(self.doc),'bbox':[x,self.y,x+width,self.y+s['size_pt']*1.4]})
        self.toc.append([b.get('level',1),b['text'],len(self.doc)]);self.y+=s['size_pt']*1.4+s.get('after_pt',6)
        if full:self.frame_top=self.y
    def equation(self,b):
        a=self.assets[b['asset']];require(a['width']<=self.width+.01,'EQUATION_REQUIRES_WIDER_LAYOUT')
        require(not (a.get('contains_number') and b.get('number')),'DUPLICATED_EQUATION_NUMBER')
        key=b.get('number_style','body');size=self.style(key)['size_pt'];num=b.get('number');extra=0
        note='('+str(num)+')' if num is not None else ''
        nw=self.latin[False].text_length(note,fontsize=size);reserve=nw+6 if num is not None and a['width']<=self.width-nw-6 else 0
        self.ensure(a['height']+16+(size+3 if num is not None else 0))
        x=self.x+(self.width-reserve-a['width'])/2;dest=fitz.Rect(x,self.y+5,x+a['width'],self.y+5+a['height'])
        self.pg.show_pdf_page(dest,self.asset_pdf,a['page']-1,keep_proportion=False);note_box=None;below=False
        if num is not None:
            import numpy as np
            px=self.asset_pdf[a['page']-1].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False);ar=np.frombuffer(px.samples,np.uint8).reshape(px.height,px.width,3)
            nx=self.x+self.width-nw;preferred=a['height']/2+size*.3;baseline=None
            for yy in [preferred]+[preferred+sign*offset for offset in range(2,30,2) for sign in [-1,1]]:
                if yy<size*.7 or yy>a['height']+size*.3:continue
                l=max(0,int((nx-x-2)*2));top=max(0,int((yy-size*.8)*2));bot=min(ar.shape[0],math.ceil((yy+size*.2)*2))
                if l>=ar.shape[1] or bot<=top or not (ar[top:bot,l:].min(2)<240).any():baseline=dest.y0+yy;break
            if baseline is None:baseline=dest.y1+size;extra=size+3;below=True
            self.text(note,nx,baseline,key);note_box=[nx,baseline-size*.8,self.x+self.width,baseline+size*.25]
        self.objects.append({'kind':'equation','id':b['id'],'asset':a['id'],'page':len(self.doc),'bbox':list(dest),'number':num,'number_bbox':note_box,'number_below':below})
        self.y+=a['height']+16+extra;self.column_content=True
    def float_height(self,b,full=False):
        a=self.assets[b['asset']];width=self.W-self.left-self.right if full else self.width
        factor=min(1,width/a['width']) if b.get('fit',True) else 1
        require(a['width']*factor<=width+.01,'OBJECT_TOO_WIDE')
        key=b.get('caption_style','caption');s=self.style(key)
        lines=wrap(self.atoms(b.get('caption',''),key),width,0,s['size_pt'],s.get('leading',1.43))
        return a['height']*factor+sum(l['height'] for l in lines)+20
    def draw_float(self,b,full=False):
        a=self.assets[b['asset']];width=self.W-self.left-self.right if full else self.width;x=self.left if full else self.x
        factor=min(1,width/a['width']) if b.get('fit',True) else 1
        key=b.get('caption_style','caption');s=self.style(key);lines=wrap(self.atoms(b.get('caption',''),key),width,0,s['size_pt'],s.get('leading',1.43))
        if b['kind']=='table':
            for line in lines:self.line(line,width,key,b['id']+'-caption',False,full)
            self.y+=4
        ww,hh=a['width']*factor,a['height']*factor;dest=fitz.Rect(x+(width-ww)/2,self.y+3,x+(width+ww)/2,self.y+3+hh)
        self.pg.show_pdf_page(dest,self.asset_pdf,a['page']-1,keep_proportion=False)
        self.objects.append({'kind':b['kind'],'id':b['id'],'asset':a['id'],'page':len(self.doc),'bbox':list(dest)})
        self.y+=hh+8
        if b['kind']!='table':
            for line in lines:self.line(line,width,key,b['id']+'-caption',False,full)
        self.y+=9
    def floating(self,b):
        full=b.get('full_width',False);height=self.float_height(b,full)
        require(height<=self.limit-self.top,'FIGURE_AND_CAPTION_TALLER_THAN_PAGE')
        if full:self.wide.append(b)
        elif self.y+height<=self.limit:self.draw_float(b)
        else:self.pending.append(b)
    def flush_float(self):
        if self.pending and self.y+self.float_height(self.pending[0])<=self.limit:self.draw_float(self.pending.pop(0))
    def finish_floats(self):
        while self.pending or self.wide:
            if self.pending and self.y+self.float_height(self.pending[0])<=self.limit:self.draw_float(self.pending.pop(0))
            else:self.advance()
    def references(self,b):
        self.finish_floats();entries=b['entries'];placed=[]
        def put(entry):
            a=self.assets[entry['asset']];require(a['width']<=self.width+.01,'REFERENCE_TOO_WIDE')
            self.ensure(a['height']+4);dest=fitz.Rect(self.x,self.y,self.x+a['width'],self.y+a['height'])
            self.pg.show_pdf_page(dest,self.asset_pdf,a['page']-1,keep_proportion=False)
            row={'kind':'reference','id':entry['id'],'block':b['id'],'asset':a['id'],'page':len(self.doc),'bbox':list(dest)}
            self.objects.append(row);placed.append(row);self.y+=a['height']+4
        for entry in entries:put(entry)
        last=[r for r in placed if r['page']==len(self.doc)]
        if b.get('balance_last_page',True) and self.columns==2 and len(last)>2 and min(r['bbox'][1] for r in last)<self.top+1:
            heights=[r['bbox'][3]-r['bbox'][1]+4 for r in last];total=sum(heights);cut=min(range(1,len(last)),key=lambda n:abs(sum(heights[:n])-total/2))
            chosen=[next(e for e in entries if e['id']==r['id']) for r in last];lastpage=len(self.doc)
            self.objects[:]=[r for r in self.objects if not (r['kind']=='reference' and r['page']==lastpage)]
            self.doc.delete_page(lastpage-1);self.newpage()
            for j,entry in enumerate(chosen):
                if j==cut:self.col=1;self.y=self.top
                put(entry)

def render(plan_path,out_dir):
    plan_path=Path(plan_path).resolve();plan=read(plan_path);base=plan_path.parent
    manifest_path=resolve(base,plan['assets_manifest']);manifest=read(manifest_path);pdf=resolve(manifest_path.parent,manifest['pdf'])
    require(sha(pdf)==manifest['pdf_sha256'],'ASSET_PDF_CHANGED');validate(plan,manifest)
    out=Path(out_dir).resolve();require(not out.exists(),'JOB_EXISTS');out.mkdir(parents=True)
    shutil.copyfile(pdf,out/'assets.pdf');write(out/'assets.json',manifest);write(out/'plan.json',plan)
    ap=fitz.open(out/'assets.pdf');r=Reader(plan,manifest,ap)
    try:
        for j,b in enumerate(plan['blocks']):
            following=plan['blocks'][j+1] if j+1<len(plan['blocks']) else None
            after=r.assets[following['asset']]['height']+16 if following and following['kind']=='equation' else 0
            if b['kind']=='paragraph':r.paragraph(b,after)
            elif b['kind']=='heading':r.heading(b,following)
            elif b['kind']=='equation':r.equation(b)
            elif b['kind'] in ['figure','table']:r.floating(b)
            elif b['kind']=='references':r.references(b)
            elif b['kind']=='page_break':r.finish_floats();r.newpage()
        r.finish_floats()
        toc=[];prev=1
        for level,title,page in r.toc:
            level=1 if not toc else min(level,prev+1);toc.append([level,title,page]);prev=level
        if toc:r.doc.set_toc(toc)
        r.doc.set_metadata({'title':plan.get('title','中文论文阅读版'),'subject':'Reader edition: source vector objects, reflowed translated prose'})
        candidate=out/'candidate.pdf';r.doc.save(candidate,garbage=1,deflate=True)
        contract={'plan_sha256':digest(plan),'assets_manifest_sha256':digest(manifest),'asset_pdf_sha256':sha(out/'assets.pdf'),
                  'fonts_sha256':{key:sha(font_path(plan['fonts'][key])) for key in ['regular','bold']}}
        write(out/'contract.json',contract)
        write(out/'layout.json',{'candidate_sha256':sha(candidate),'pages':len(r.doc),'locations':r.locations,'objects':r.objects,'text_segments':r.text_segments})
        write(out/'review.json',{'candidate_sha256':sha(candidate),'reviewer':'','method':'','units':[{'id':u['id'],'meaning':'pending','numbers':'pending','structure':'pending','evidence':''} for u in plan['units']],
            'visual':{'status':'pending','pages':[],'readability':'pending','evidence':''},'unresolved':[]})
    finally:r.doc.close();ap.close()
    d=fitz.open(candidate);(out/'pages').mkdir()
    for j,pg in enumerate(d):pg.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(out/'pages'/f'{j+1:04d}.png')
    d.close();return audit(out)

def audit(job):
    job=Path(job);p=read(job/'plan.json');m=read(job/'assets.json');c=read(job/'contract.json');l=read(job/'layout.json');issues=[]
    require(c['plan_sha256']==digest(p) and c['assets_manifest_sha256']==digest(m) and c['asset_pdf_sha256']==sha(job/'assets.pdf'),'FROZEN_READER_INPUT_CHANGED')
    require(c.get('fonts_sha256')=={key:sha(font_path(p['fonts'][key])) for key in ['regular','bold']},'FROZEN_READER_FONT_CHANGED')
    validate(p,m);actual=sha(job/'candidate.pdf')
    if actual!=l['candidate_sha256']:issues.append({'kind':'candidate_hash_changed'})
    pg=p['page'];left,top,right,bottom=pg['margins'];area=fitz.Rect(left,top,pg['width']-right,pg['height']-bottom)
    checks=l['locations']+[o for o in l['objects'] if o['kind']!='inline_math']
    for j,a in enumerate(checks):
        rr=rect(a['bbox'])
        if not (area+(-.4,-.4,.4,.4)).contains(rr):issues.append({'kind':'outside_reading_area','id':a['id'],'page':a['page']})
        if a['kind']=='line' and a['text'][:1] and a['text'][:1] in CLOSE:issues.append({'kind':'line_start_closing_mark','id':a['id'],'page':a['page']})
        for b in checks[:j]:
            if a['page']!=b['page']:continue
            overlap=rr&rect(b['bbox'])
            if overlap.width>.5 and overlap.height>.5:issues.append({'kind':'object_overlap','a':a['id'],'b':b['id'],'page':a['page']})
    for h in [x for x in l['locations'] if x['kind']=='heading']:
        following=[x for x in checks if x['page']==h['page'] and x['id']!=h['id'] and x['kind']!='heading' and abs(x['bbox'][0]-h['bbox'][0])<2 and h['bbox'][3]<=x['bbox'][1]<h['bbox'][3]+70]
        # A centred equation is valid follow-on material despite its x offset.
        following+= [x for x in l['objects'] if x['kind'] in ['equation','figure','table'] and x['page']==h['page'] and (rect(x['bbox'])&rect(h['bbox'])).width>min(rect(x['bbox']).width,rect(h['bbox']).width)/2 and h['bbox'][3]<=x['bbox'][1]<h['bbox'][3]+70]
        if not following:issues.append({'kind':'orphan_heading','id':h['id'],'page':h['page']})
    # Check glyph positions against the frozen vector asset, not source page
    # coordinates (which intentionally change in a reader edition).
    d=fitz.open(job/'candidate.pdf');ad=fitz.open(job/'assets.pdf');am={a['id']:a for a in m['assets']};checked=0;traces={};page_spans={}
    def spans(page):
        if page not in page_spans:page_spans[page]=d[page-1].get_texttrace()
        return page_spans[page]
    for o in l['objects']:
        if o['kind'] not in ['inline_math','equation']:continue
        a=am[o['asset']];sp=ad[a['page']-1];dest=rect(o['bbox']);sx=dest.width/sp.rect.width;sy=dest.height/sp.rect.height
        if abs(sx-1)>.001 or abs(sy-1)>.001:issues.append({'kind':'math_scaled','id':o['id']})
        if o['page'] not in traces:
            lookup=collections.defaultdict(list)
            for ss in spans(o['page']):
                for cc in ss['chars']:lookup[(family(ss['font']),cc[1])].append((ss['size'],cc[2]))
            traces[o['page']]=lookup
        for ss in sp.get_texttrace():
            for cc in ss['chars']:
                x,y=cc[2]
                if cc[0]==32 or not sp.rect.contains(fitz.Point(x,y)):continue
                checked+=1;xp,yp=dest.x0+x*sx,dest.y0+y*sy
                if not any(abs(sz-ss['size'])<.002 and abs(ox-xp)<.04 and abs(oy-yp)<.04 for sz,(ox,oy) in traces[o['page']].get((family(ss['font']),cc[1]),[])):
                    issues.append({'kind':'math_glyph_missing','asset':a['id'],'page':o['page'],'glyph_id':cc[1]})
    # Text really exists in the saved PDF: use font, codepoint and origin for
    # native text runs so inserted forms do not scramble extraction order.
    native={};font_cache={}
    # MuPDF may report the PostScript family without the "Regular" suffix.
    canonical=lambda name:re.sub(r'regular$','',re.sub(r'[^a-z0-9]','',family(name).lower()))
    for s in l['text_segments']:
        page=s['page']
        if page not in native:
            lookup=collections.defaultdict(list)
            for ss in spans(page):
                for cc in ss['chars']:lookup[(cc[0],canonical(ss['font']))].append((ss['size'],cc[2]))
            native[page]=lookup
        name=s['font']
        if name not in font_cache:
            if name in ['ZHR','ZHB']:font_cache[name]=fitz.Font(fontfile=font_path(p['fonts']['bold' if name=='ZHB' else 'regular']))
            else:font_cache[name]=fitz.Font(name)
        ff=font_cache[name];xx,yy=s['point'];size=s['size_pt'];ok=True
        for ch in s['text']:
            if not ch.isspace() and not any(abs(sz-size)<.002 and abs(ox-xx)<.04 and abs(oy-yy)<.04 for sz,(ox,oy) in native[page].get((ord(ch),canonical(ff.name)),[])):ok=False
            xx+=ff.text_length(ch,fontsize=size)
        if not ok:issues.append({'kind':'target_text_font_or_origin_changed','page':page,'text':s['text']})
    for j,dp in enumerate(d):
        if ASSET.search(dp.get_text()):issues.append({'kind':'unresolved_asset_token','page':j+1})
    result={'status':'pass' if not issues else 'fail','candidate_sha256':actual,'pages':len(d),'unit_count':len(p['units']),
        'original_math_glyphs_checked':checked,'issues':issues,'scope':'reader layout and frozen-asset coverage; not semantic truth or original-coordinate equivalence'}
    d.close();ad.close();write(job/'audit.json',result);return result

def publish(job,out):
    job=Path(job);out=Path(out).resolve();a=audit(job);require(a['status']=='pass','READER_TECHNICAL_GATE_FAILED')
    p=read(job/'plan.json');r=read(job/'review.json');require(r['candidate_sha256']==a['candidate_sha256'],'REVIEW_STALE')
    require(r.get('reviewer','').strip() and r.get('method','').strip(),'REAL_REVIEW_REQUIRED')
    require(collections.Counter(x['id'] for x in r['units'])==collections.Counter(u['id'] for u in p['units']),'REVIEW_UNIT_COVERAGE')
    require(all(x.get(k)=='pass' for x in r['units'] for k in ['meaning','numbers','structure']) and all(x.get('evidence','').strip() for x in r['units']),'SEMANTIC_REVIEW_PENDING')
    v=r['visual'];require(v.get('status')==v.get('readability')=='pass' and set(v.get('pages',[]))==set(range(1,a['pages']+1)) and v.get('evidence','').strip(),'VISUAL_READABILITY_REVIEW_PENDING')
    require(r.get('unresolved')==[],'UNRESOLVED_ITEMS');require(not out.exists(),'OUTPUT_EXISTS')
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('xb') as fp:fp.write((job/'candidate.pdf').read_bytes())
    write(job/'delivery.json',{'output':str(out),'sha256':sha(out),'audit_sha256':digest(a),'review_sha256':digest(r),'edition':'reader'})
    return {'output':str(out),'sha256':sha(out)}

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    a=s.add_parser('render');a.add_argument('plan');a.add_argument('out_dir')
    a=s.add_parser('audit');a.add_argument('job')
    a=s.add_parser('publish');a.add_argument('job');a.add_argument('output')
    a=p.parse_args()
    result=render(a.plan,a.out_dir) if a.command=='render' else audit(a.job) if a.command=='audit' else publish(a.job,a.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result.get('status')=='fail':raise SystemExit(2)
if __name__=='__main__':main()
