"""Optional bounded font subsetting; exact pixels/text/TOC or unchanged fallback."""
import argparse,json,os,subprocess,sys,time,shutil,uuid,contextlib
from pathlib import Path
from reader_common import *

@contextlib.contextmanager
def task_temp(parent):
    # Python 3.14's Windows mkdir(mode=0700), used by TemporaryDirectory,
    # denied this managed sandbox access even to its own worker.log. A normal
    # task-local directory inherits the already authorized workspace ACL.
    parent=Path(parent).resolve();path=parent/('.compact-'+uuid.uuid4().hex);path.mkdir()
    try:yield path
    finally:
        resolved=path.resolve()
        require(resolved.parent==parent and resolved.name.startswith('.compact-'),'TEMP_CLEANUP_OUTSIDE_PARENT')
        for name in ['worker.log','subset.pdf']:
            p=resolved/name
            if p.is_file():p.unlink()
        try:resolved.rmdir()
        except OSError:pass  # Never recursively delete unexpected files.

def compact(source,output,timeout=60,scale=2):
    source=Path(source).resolve();output=Path(output).resolve()
    require(source.is_file() and not output.exists() and source!=output,'SOURCE_OR_OUTPUT_INVALID')
    require(0<timeout<=3600 and 1<=scale<=4,'LIMIT_INVALID')
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'source_sha256':sha(source),'source_bytes':source.stat().st_size,'pages':[],'issues':[],'scale':scale}
    with task_temp(output.parent) as temp:
        temp=Path(temp);candidate=temp/'subset.pdf';log=temp/'worker.log'
        command=[sys.executable,str(Path(__file__).resolve()),'_worker',str(source),str(candidate)]
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        try:
            with log.open('wb') as fp:
                proc=subprocess.Popen(command,stdout=fp,stderr=subprocess.STDOUT,creationflags=creationflags)
                deadline=time.monotonic()+timeout
                while proc.poll() is None and time.monotonic()<deadline:time.sleep(.1)
                if proc.poll() is None:
                    proc.kill();proc.wait();report['issues'].append({'kind':'worker_timeout','seconds':timeout})
                elif proc.returncode:report['issues'].append({'kind':'worker_error','exit_code':proc.returncode})
        except OSError as exc:
            report['issues'].append({'kind':'worker_start_error','detail':str(exc)})
        report['worker_log']=log.read_text(encoding='utf-8',errors='replace')[-4000:] if log.exists() else ''
        if not report['issues']:
            import numpy as np
            try:
                a=fitz.open(source);b=fitz.open(candidate)
                require(len(a)==len(b),'PAGE_COUNT_CHANGED')
                require(a.get_toc()==b.get_toc(),'BOOKMARKS_CHANGED')
                for j in range(len(a)):
                    ap,bp=a[j],b[j];require(list(ap.rect)==list(bp.rect) and ap.rotation==bp.rotation,'GEOMETRY_CHANGED')
                    aa=ap.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False);bb=bp.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                    x=np.frombuffer(aa.samples,np.uint8);y=np.frombuffer(bb.samples,np.uint8)
                    require(x.shape==y.shape,'RASTER_GEOMETRY_CHANGED')
                    changed=int(np.any(x.reshape(-1,3)!=y.reshape(-1,3),axis=1).sum());same=ap.get_text()==bp.get_text()
                    report['pages'].append({'page':j+1,'changed_pixels':changed,'text_equal':same})
                    if changed or not same:report['issues'].append({'kind':'not_equivalent','page':j+1})
                a.close();b.close()
            except Exception as exc:report['issues'].append({'kind':'comparison_error','detail':str(exc)})
        better=candidate.is_file() and candidate.stat().st_size<source.stat().st_size
        use_subset=not report['issues'] and better
        chosen=candidate if use_subset else source
        with output.open('xb') as fp:
            with chosen.open('rb') as src:shutil.copyfileobj(src,fp)
        report.update(status='equivalent-subset' if use_subset else 'unchanged-fallback',output=str(output),output_sha256=sha(output),output_bytes=output.stat().st_size)
    write(output.with_suffix('.compression.json'),report);return report

def main():
    if len(sys.argv)>1 and sys.argv[1]=='_worker':
        with fitz.open(sys.argv[2]) as d:
            d.subset_fonts()
            # Do not use garbage=4 here: duplicate-stream comparison stalled
            # on the Cox document after native font subsetting.
            d.save(sys.argv[3],garbage=1,deflate=True)
        return
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output');p.add_argument('--timeout',type=float,default=60);p.add_argument('--scale',type=float,default=2)
    a=p.parse_args();r=compact(a.source,a.output,a.timeout,a.scale);print(json.dumps({k:v for k,v in r.items() if k not in ['pages','worker_log']},ensure_ascii=False))
if __name__=='__main__':main()
