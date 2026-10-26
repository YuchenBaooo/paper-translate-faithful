"""Compose reviewed native vector pieces; do not infer semantic math joins."""
import argparse,json,io,re
from pathlib import Path
from reader_common import *

def glyph_bounds(doc,page_no,font_name=None):
    """Actual embedded outline bounds, including legacy CMEX encodings."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.cffLib import CFFFontSet
    from fontTools.ttLib import TTFont
    pg=doc[page_no-1];fonts={family(x[3]):x for x in pg.get_fonts()};cache={};result=[]
    for span in pg.get_texttrace():
        fn=span['font']
        if font_name and fn!=font_name:continue
        try:
            if fn not in cache:
                info=fonts[family(fn)];_,ext,_,buf=doc.extract_font(info[0])
                if ext=='cff':
                    c=CFFFontSet();c.decompile(io.BytesIO(buf),None);top=c[c.fontNames[0]]
                    require(list(top.FontMatrix)==[.001,0,0,.001,0,0],'NONSTANDARD_CFF_MATRIX_REQUIRES_ADAPTER')
                    cache[fn]=('cff',top,1000)
                elif ext in ['ttf','otf']:
                    tt=TTFont(io.BytesIO(buf));cache[fn]=('ttf',tt,tt['head'].unitsPerEm)
                else:raise ValueError('unsupported embedded font '+ext)
            typ,font,upm=cache[fn]
            for char in span['chars']:
                uni,gid,(x,y),nominal=char
                if gid<0:continue
                if typ=='cff':
                    pen=BoundsPen(None);font.CharStrings[font.charset[gid]].draw(pen)
                else:
                    gs=font.getGlyphSet();pen=BoundsPen(gs);gs[font.getGlyphOrder()[gid]].draw(pen)
                if not pen.bounds:continue
                require(abs(span['dir'][0]-1)<1e-5 and abs(span['dir'][1])<1e-5,'ROTATED_GLYPH_REQUIRES_ADAPTER')
                x0,y0,x1,y1=pen.bounds;s=span['size']/upm
                ink=[x+x0*s,y-y1*s,x+x1*s,y-y0*s]
                result.append({'font':fn,'glyph_id':gid,'unicode':uni,'origin':[x,y],
                    'size_pt':span['size'],'nominal_bbox':list(nominal),'ink_bbox':ink,'intersects_page':fitz.Rect(ink).intersects(pg.rect)})
        except Exception as exc:
            result.append({'font':fn,'status':'unsupported','reason':str(exc)})
    return result

def compose(spec_path,out_dir):
    p=Path(spec_path).resolve();spec=read(p);out=Path(out_dir).resolve()
    require(not out.exists(),'OUTPUT_DIRECTORY_EXISTS');docs,src=sources(spec,p.parent)
    ids=[a['id'] for a in spec['assets']];require(len(ids)==len(set(ids)),'DUPLICATE_ASSET_ID')
    output=fitz.open();entries=[]
    try:
        for a in spec['assets']:
            require(a.get('evidence','').strip(),'CLEAN_CLIP_EVIDENCE_REQUIRED:'+a['id'])
            require(a['role'] in ['inline_math','equation','figure','table','reference','metadata'],'ASSET_ROLE')
            mode=a.get('composition','horizontal');require(mode in ['horizontal','vertical'],'COMPOSITION')
            pieces=[]
            for piece in a['pieces']:
                doc=docs[piece['source']];pi=piece['page']-1;r=rect(piece['bbox'])
                require(0<=pi<len(doc) and (doc[pi].rect+(-.01,-.01,.01,.01)).contains(r),'PIECE_OUTSIDE_PAGE:'+a['id'])
                r&=doc[pi].rect  # float32 PDF serialization, not permission to crop outside a page
                require(doc[pi].rotation==0,'ROTATED_PAGE_REQUIRES_ADAPTER')
                baseline=float(piece.get('baseline',r.y1));require(math.isfinite(baseline),'BASELINE')
                pieces.append((doc,pi,r,baseline,piece))
            require(bool(pieces),'EMPTY_ASSET')
            gap=float(a.get('gap_pt',.6));require(0<=gap<=20,'PIECE_GAP')
            if mode=='horizontal':
                asc=max(base-r.y0 for _,_,r,base,_ in pieces);desc=max(r.y1-base for _,_,r,base,_ in pieces)
                width=sum(r.width for _,_,r,_,_ in pieces)+gap*(len(pieces)-1);height=asc+desc
                positions=[];x=0
                for doc,pi,r,base,piece in pieces:
                    positions.append((doc,pi,r,fitz.Rect(x,asc-(base-r.y0),x+r.width,asc+(r.y1-base))))
                    x+=r.width+gap
                baseline=asc
            else:
                width=max(float(piece.get('offset_x',0))+r.width for _,_,r,_,piece in pieces)
                height=sum(r.height for _,_,r,_,_ in pieces)+gap*(len(pieces)-1);positions=[];y=0
                for doc,pi,r,base,piece in pieces:
                    x=float(piece.get('offset_x',0));require(x>=0,'NEGATIVE_OFFSET')
                    positions.append((doc,pi,r,fitz.Rect(x,y,x+r.width,y+r.height)));y+=r.height+gap
                baseline=float(a.get('baseline_pt',height*.7))
            require(width>0 and height>0,'INVALID_ASSET_SIZE')
            pg=output.new_page(width=width,height=height)
            for doc,pi,r,dest in positions:pg.show_pdf_page(dest,doc,pi,clip=r,keep_proportion=False)
            entries.append({'id':a['id'],'role':a['role'],'page':len(output),'width':width,'height':height,
                'baseline_pt':baseline,'source_units':a.get('source_units',[]),'evidence':a['evidence'],
                'composition':mode,'pieces':a['pieces'],'provenance':a.get('provenance',[]),'contains_number':a.get('contains_number',False)})
        out.mkdir(parents=True);pdf=out/'assets.pdf';output.save(pdf,garbage=1,deflate=True)
        write(out/'assets.json',{'version':1,'pdf':'assets.pdf','pdf_sha256':sha(pdf),
            'sources':src,'spec_sha256':sha(p),'assets':entries,'status':'candidate-requires-source-and-visual-review'})
        rendered=fitz.open(pdf)
        for j,pg in enumerate(rendered):pg.get_pixmap(matrix=fitz.Matrix(3,3),alpha=False).save(out/f'asset-{j+1:03d}.png')
        rendered.close()
        return {'manifest':str(out/'assets.json'),'assets':len(entries)}
    finally:
        output.close()
        for d in docs.values():d.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    a=s.add_parser('compose');a.add_argument('spec');a.add_argument('out_dir')
    a=s.add_parser('glyph-bounds');a.add_argument('pdf');a.add_argument('--page',type=int,required=True);a.add_argument('--font');a.add_argument('--output',required=True)
    a=p.parse_args()
    if a.command=='compose':result=compose(a.spec,a.out_dir)
    else:
        with fitz.open(a.pdf) as d:rows=glyph_bounds(d,a.page,a.font)
        write(a.output,{'source_sha256':sha(a.pdf),'page':a.page,'glyphs':rows});result={'output':a.output,'glyphs':len(rows)}
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
