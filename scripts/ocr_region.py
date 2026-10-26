"""Lossless PDF region extraction and optional local Tesseract TSV proposal.

The result is deliberately unreviewed. Never replaces source or freezes a job.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import paperflow as f

def pixel_to_pdf(bbox,left,top,width,height,dpi):
    scale=dpi/72
    return [bbox[0]+left/scale,bbox[1]+top/scale,
            bbox[0]+(left+width)/scale,bbox[1]+(top+height)/scale]

def extract(source,page_no,bbox,out,dpi=300,run_ocr=False,language='eng'):
    out=Path(out).resolve();f.require(not out.exists(),'OUTPUT_DIRECTORY_EXISTS')
    f.require(dpi>=144 and dpi<=1200,'DPI_OUTSIDE_SUPPORTED_RANGE')
    rect=f.box(bbox)
    with f.pm().open(source) as doc:
        f.require(1<=page_no<=len(doc),'PAGE_INVALID')
        page=doc[page_no-1]
        f.require(page.rotation==0 and page.cropbox.x0==0 and page.cropbox.y0==0,'ROTATED_REGION_REQUIRES_EXPLICIT_COORDINATE_TRANSFORM')
        f.require(f.inside(bbox,list(page.rect)),'REGION_OUTSIDE_PAGE')
        out.mkdir(parents=True)
        pix=page.get_pixmap(matrix=f.pm().Matrix(dpi/72,dpi/72),clip=rect,alpha=False)
        pix.save(out/'region.png')
        # Pixmap clips on integer device pixels. Its actual origin, rather than
        # a fractional requested bbox, is the mapping reference for TSV pixels.
        actual=[pix.x*72/dpi,pix.y*72/dpi,(pix.x+pix.width)*72/dpi,(pix.y+pix.height)*72/dpi]
    proposal={'source_sha256':f.digest(Path(source)),'page':page_no,'requested_bbox':bbox,
              'render_bbox':actual,'dpi':dpi,'image_sha256':f.digest(out/'region.png'),
              'status':'unreviewed','words':[],'notes':'Review source pixels, reading order, symbols and coordinates before using in plan.json'}
    if run_ocr:
        cmd=shutil.which('tesseract');f.require(bool(cmd),'TESSERACT_NOT_INSTALLED: keep PNG for visual or alternate OCR route')
        result=subprocess.run([cmd,str(out/'region.png'),'stdout','-l',language,'tsv'],capture_output=True,text=True,encoding='utf-8',timeout=180)
        f.require(result.returncode==0,'TESSERACT_FAILED:'+result.stderr[-1200:])
        (out/'ocr.tsv').write_text(result.stdout,encoding='utf-8')
        for row in csv.DictReader(io.StringIO(result.stdout),delimiter='\t'):
            text=row.get('text','').strip()
            if not text:continue
            proposal['words'].append({'source':text,'confidence':float(row['conf']),
                                      'bbox':pixel_to_pdf(actual,*[int(row[k]) for k in ['left','top','width','height']],dpi),
                                      'reviewed':False})
    f.write(out/'ocr-proposal.json',proposal)
    return {'image':str(out/'region.png'),'proposal':str(out/'ocr-proposal.json'),'words':len(proposal['words']),'status':'unreviewed'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output')
    p.add_argument('--page',type=int,required=True);p.add_argument('--bbox',type=float,nargs=4,required=True)
    p.add_argument('--dpi',type=int,default=300);p.add_argument('--tesseract',action='store_true');p.add_argument('--language',default='eng')
    a=p.parse_args()
    try:print(json.dumps(extract(a.source,a.page,a.bbox,a.output,a.dpi,a.tesseract,a.language),ensure_ascii=False,indent=2));return 0
    except (f.Blocked,subprocess.TimeoutExpired) as e:print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
