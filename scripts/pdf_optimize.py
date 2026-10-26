"""Vector-preserving PDF performance optimization, with per-form rollback.

Merge fragmented page streams, prune clipped-away Form content, subset fonts.
This is not rasterization. The original is never overwritten. All-page exact
pixels/text/navigation gates are mandatory before releasing the output.
"""
import argparse,collections,json,math,os,re,shutil,subprocess,sys,time,uuid
from pathlib import Path
import numpy as np
from reader_common import *

def profile(doc):
    return {'pages':len(doc),'xref_count':doc.xref_length(),'content_streams':sum(len(p.get_contents()) for p in doc),
            'root_forms':sum(sum(x[2]==0 for x in p.get_xobjects()) for p in doc)}

def pix_equal(a,b):
    if a.width!=b.width or a.height!=b.height or a.n!=b.n:return False
    return a.samples==b.samples

def replace_form_with_clipped(doc,xref):
    box=doc.xref_get_key(xref,'BBox')[1]
    values=list(map(float,re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?',box)))
    area=rect(values);content=doc.xref_stream(xref);resources=doc.xref_get_key(xref,'Resources')[1]
    require(resources!='null','FORM_WITHOUT_RESOURCES_REQUIRES_ADAPTER')
    count=len(doc)
    try:
        # Some legacy fonts paint a sliver beyond nominal glyph metrics. Keep
        # a conservative halo for culling, while retaining the original Form
        # BBox as the actual final clip. The halo never enlarges visible art.
        cull_area=area+(-2,-2,2,2)
        cull_box='['+' '.join(format(v,'.12g') for v in cull_area)+']'
        pg=doc.new_page(width=cull_area.width,height=cull_area.height)
        doc.xref_set_key(pg.xref,'MediaBox',cull_box);doc.xref_set_key(pg.xref,'Resources',resources)
        stream=doc.get_new_xref();doc.update_object(stream,'<<>>');doc.update_stream(stream,content);pg.set_contents(stream);pg=doc.reload_page(pg)
        # clip_to_rect instances child forms, retaining the original root BBox,
        # Matrix, Group and dictionary. Only its resource/content are replaced.
        pg.clip_to_rect(pg.rect)
        new_resources=doc.xref_get_key(pg.xref,'Resources')[1];new_content=pg.read_contents()
        doc.xref_set_key(xref,'Resources',new_resources);doc.update_stream(xref,new_content)
    finally:
        if len(doc)>count:doc.delete_page(len(doc)-1)

def restore(doc,xref,object_text,stream):
    doc.update_object(xref,object_text);doc.update_stream(xref,stream)

def merge_page_streams(doc):
    for page in doc:
        data=page.read_contents()
        if len(page.get_contents())<=1:continue
        x=doc.get_new_xref();doc.update_object(x,'<<>>');doc.update_stream(x,data);page.set_contents(x)

def worker(source,out,report_path,scales,prune=True,subset=True):
    a=fitz.open(source);d=fitz.open(source)
    require(not a.is_encrypted,'ENCRYPTED_INPUT_NOT_SUPPORTED')
    require(a.get_sigflags()<=0,'SIGNED_DOCUMENT_REQUIRES_ADAPTER')
    require(a.xref_get_key(a.pdf_catalog(),'StructTreeRoot')[1]=='null','TAGGED_DOCUMENT_REQUIRES_ADAPTER')
    if prune:require(a.xref_get_key(a.pdf_catalog(),'OCProperties')[1]=='null','OPTIONAL_CONTENT_REQUIRES_ADAPTER')
    require(all(not p.rotation for p in a),'ROTATED_PAGES_REQUIRE_ADAPTER')
    report={'source_sha256':sha(source),'before':profile(a),'forms':[],'scales':scales,'steps':[]}
    owners=collections.defaultdict(set)
    page_roots=[]
    for i,pg in enumerate(a):
        roots=[x for x in pg.get_xobjects() if x[2]==0];page_roots.append(roots)
        for x in roots:owners[x[0]].add(i)
    if prune:
        require(hasattr(fitz.Page,'clip_to_rect'),'CLIP_TO_RECT_UNAVAILABLE')
        for i,roots in enumerate(page_roots):
            native_text=a[i].get_text();original_display=a[i].get_displaylist()
            for form in roots:
                x=form[0];result={'page':i+1,'xref':x}
                if len(owners[x])>1:
                    result.update(status='kept',reason='shared-root-needs-multipage-adapter');report['forms'].append(result);continue
                area=(fitz.Rect(form[3])*a[i].transformation_matrix)+(-2,-2,2,2);area&=a[i].rect
                if area.is_empty:
                    result.update(status='kept',reason='outside-page');report['forms'].append(result);continue
                old_object=d.xref_object(x);old_stream=d.xref_stream(x)
                try:
                    replace_form_with_clipped(d,x)
                    if d[i].get_text()!=native_text:raise InvalidPlan('native_text_changed')
                    display=d[i].get_displaylist()
                    for scale in scales:
                        aa=original_display.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=area,alpha=False)
                        bb=display.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=area,alpha=False)
                        if not pix_equal(aa,bb):raise InvalidPlan('regional_pixels_changed_at_'+str(scale))
                    result.update(status='pruned')
                except Exception as exc:
                    restore(d,x,old_object,old_stream);result.update(status='kept',reason=str(exc))
                report['forms'].append(result)
            print(json.dumps({'page':i+1,'root_forms':len(roots),'pruned':sum(r['page']==i+1 and r['status']=='pruned' for r in report['forms'])}),flush=True)
        report['steps'].append('per-form native-text and regional-pixel gated clipping')
    merge_page_streams(d);report['steps'].append('lossless page content-stream concatenation')
    if subset:d.subset_fonts();report['steps'].append('native font subsetting')
    # Do not compare duplicate streams (garbage=4): it can stall on nested forms.
    d.save(out,garbage=2,deflate=True)
    d.close();a.close();write(report_path,report)

def verify(source,candidate,scales):
    a=fitz.open(source);b=fitz.open(candidate);issues=[];rows=[]
    require(len(a)==len(b),'PAGE_COUNT_CHANGED')
    if a.get_toc()!=b.get_toc():issues.append({'kind':'bookmarks_changed'})
    if a.metadata!=b.metadata:issues.append({'kind':'metadata_changed'})
    for i in range(len(a)):
        x,y=a[i],b[i];same_text=x.get_text()==y.get_text();same_links=x.get_links()==y.get_links()
        # xref IDs legitimately change on garbage collection; compare link payload.
        clean=lambda links:[{k:v for k,v in row.items() if k not in ['xref','id']} for row in links]
        same_links=clean(x.get_links())==clean(y.get_links())
        row={'page':i+1,'text_equal':same_text,'links_equal':same_links,'pixels':[]}
        if not same_text or not same_links or list(x.rect)!=list(y.rect) or x.rotation!=y.rotation:issues.append({'kind':'page_contract_changed','page':i+1})
        xd=x.get_displaylist();yd=y.get_displaylist()
        for scale in scales:
            aa=xd.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False);bb=yd.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            count=0
            if not pix_equal(aa,bb):
                if aa.width==bb.width and aa.height==bb.height:count=int(np.any(np.frombuffer(aa.samples,np.uint8).reshape(-1,aa.n)!=np.frombuffer(bb.samples,np.uint8).reshape(-1,bb.n),axis=1).sum())
                else:count=-1
                issues.append({'kind':'pixels_changed','page':i+1,'scale':scale,'changed_pixels':count})
            row['pixels'].append({'scale':scale,'changed_pixels':count})
        rows.append(row)
    result={'status':'pass' if not issues else 'fail','pages':rows,'issues':issues,'after':profile(b)}
    a.close();b.close();return result

def optimize(source,output,timeout=300,scales=(2,3),prune=True,subset=True):
    source=Path(source).resolve();output=Path(output).resolve()
    require(source.is_file() and source!=output and not output.exists(),'SOURCE_OR_OUTPUT_INVALID')
    require(0<timeout<=3600 and scales and all(math.isfinite(s) and 1<=s<=6 for s in scales),'LIMIT_INVALID')
    output.parent.mkdir(parents=True,exist_ok=True)
    job=output.parent/('.pdf-performance-'+uuid.uuid4().hex);job.mkdir()
    candidate=job/'candidate.pdf';report_file=job/'structure.json';log=job/'worker.log'
    cmd=[sys.executable,str(Path(__file__).resolve()),'_worker',str(source),str(candidate),str(report_file),','.join(map(str,scales)),str(int(prune)),str(int(subset))]
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with log.open('wb') as fp:
        proc=subprocess.Popen(cmd,stdout=fp,stderr=subprocess.STDOUT,creationflags=flags)
        try:proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:proc.kill();proc.wait();raise InvalidPlan('PERFORMANCE_WORKER_TIMEOUT; original unchanged; evidence: '+str(job))
    require(proc.returncode==0,'PERFORMANCE_WORKER_FAILED: '+str(log))
    result=read(report_file);result['verification']=verify(source,candidate,scales)
    result.update(source=str(source),output=str(output),source_bytes=source.stat().st_size,candidate_bytes=candidate.stat().st_size,evidence_dir=str(job))
    report_path=output.with_suffix('.performance.json');write(report_path,result)
    require(result['verification']['status']=='pass','PERFORMANCE_EQUIVALENCE_FAILED: '+str(report_path))
    with output.open('xb') as out:
        with candidate.open('rb') as src:shutil.copyfileobj(src,out)
    result.update(status='equivalent-vector-output',output_sha256=sha(output));write(report_path,result)
    return {k:result[k] for k in ['status','output','source_bytes','candidate_bytes','output_sha256','before']}|{'after':result['verification']['after'],'pruned_forms':sum(r['status']=='pruned' for r in result['forms']),'kept_forms':sum(r['status']=='kept' for r in result['forms'])}

def main():
    if len(sys.argv)>1 and sys.argv[1]=='_worker':
        worker(sys.argv[2],sys.argv[3],sys.argv[4],[float(x) for x in sys.argv[5].split(',')],bool(int(sys.argv[6])),bool(int(sys.argv[7])));return
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output');p.add_argument('--timeout',type=float,default=300);p.add_argument('--scales',type=float,nargs='+',default=[2,3]);p.add_argument('--no-prune',action='store_true');p.add_argument('--no-subset',action='store_true')
    a=p.parse_args();print(json.dumps(optimize(a.source,a.output,a.timeout,a.scales,not a.no_prune,not a.no_subset),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
