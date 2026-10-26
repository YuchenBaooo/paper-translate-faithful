"""Patch a reviewed glyph mask; never infer or redraw scientific content."""
import argparse,json
from pathlib import Path
from reader_common import *

def patch(spec_path,out_dir):
    import numpy as np
    from PIL import Image,ImageFont,ImageDraw
    p=Path(spec_path).resolve();s=read(p);out=Path(out_dir).resolve();require(not out.exists(),'OUTPUT_DIRECTORY_EXISTS')
    require(s.get('mask_reviewed') is True and s.get('evidence','').strip(),'GLYPH_MASK_REVIEW_REQUIRED')
    source=resolve(p.parent,s['source_png']);maskfile=resolve(p.parent,s['mask_png'])
    require(sha(source)==s['source_sha256'] and sha(maskfile)==s['mask_sha256'],'RASTER_INPUT_CHANGED')
    im=Image.open(source).convert('RGB');maskim=Image.open(maskfile).convert('L');require(maskim.size==im.size,'MASK_GEOMETRY')
    mask=np.asarray(maskim)>127;require(bool(mask.any()),'EMPTY_GLYPH_MASK');a=np.asarray(im).astype(float);original=a.copy()
    edit=rect(s['edit_bbox_px']);require(fitz.Rect(0,0,im.width,im.height).contains(edit),'EDIT_REGION_OUTSIDE_CROP')
    allowed=np.zeros(mask.shape,bool);allowed[math.floor(edit.y0):math.ceil(edit.y1),math.floor(edit.x0):math.ceil(edit.x1)]=True
    require(not (mask&~allowed).any(),'MASK_OUTSIDE_REVIEWED_REGION')
    protected=np.zeros(mask.shape,bool)
    for b in s.get('protected_bboxes_px',[]):
        r=rect(b);protected[max(0,int(r.y0)):min(im.height,math.ceil(r.y1)),max(0,int(r.x0)):min(im.width,math.ceil(r.x1))]=True
    require(not (mask&protected).any(),'MASK_TOUCHES_SCIENTIFIC_CONTENT')
    if s['background']=='white':a[mask]=255
    elif s['background']=='local-harmonic':
        # Only reviewed glyph pixels change; use fixed boundary values.
        a[mask]=np.median(a[~mask],axis=0)
        for _ in range(int(s.get('iterations',300))):
            padded=np.pad(a,((1,1),(1,1),(0,0)),mode='edge')
            mean=(padded[:-2,1:-1]+padded[2:,1:-1]+padded[1:-1,:-2]+padded[1:-1,2:])/4;a[mask]=mean[mask]
    else:raise InvalidPlan('BACKGROUND_ROUTE_UNSUPPORTED')
    restored=Image.fromarray(np.clip(a,0,255).astype('uint8'))
    size=float(s['font_size_pt_estimate'])*float(s['pixels_per_point']);require(size>0,'FONT_SIZE')
    font=ImageFont.truetype(font_path(s.get('font','auto-cjk')),round(size));bb=font.getbbox(s['translation'])
    glyph=Image.new('RGBA',(bb[2]-bb[0]+8,bb[3]-bb[1]+8));draw=ImageDraw.Draw(glyph)
    draw.text((4-bb[0],4-bb[1]),s['translation'],font=font,fill=tuple(s.get('text_rgb',[0,0,0]))+(255,))
    glyph=glyph.rotate(float(s.get('rotation',0)),expand=True,resample=Image.Resampling.BICUBIC)
    cx,cy=s['center_px'];xy=(round(cx-glyph.width/2),round(cy-glyph.height/2));gr=fitz.Rect(xy[0],xy[1],xy[0]+glyph.width,xy[1]+glyph.height)
    require(edit.contains(gr),'CHINESE_LABEL_EXCEEDS_REVIEWED_REGION: do not shrink silently')
    layer=Image.new('RGBA',im.size);layer.alpha_composite(glyph,xy)
    changed=(np.asarray(layer)[:,:,3]>0)|mask;require(not (changed&protected).any(),'TARGET_TOUCHES_SCIENTIFIC_CONTENT')
    result=restored.convert('RGBA');result.alpha_composite(layer);result=result.convert('RGB')
    arr=np.asarray(result);outside=int(np.any(arr!=original.astype('uint8'),axis=2)[~allowed].sum());require(outside==0,'OUTSIDE_EDIT_PIXELS_CHANGED')
    out.mkdir(parents=True);result.save(out/'candidate.png');restored.save(out/'background-repaired.png');layer.save(out/'target-layer.png')
    report={'status':'candidate-needs-visual-review','source_sha256':sha(source),'candidate_sha256':sha(out/'candidate.png'),
            'outside_edit_changed_pixels':outside,'mask_pixels':int(mask.sum()),'target_bbox_px':list(gr),'font_size_pt_estimate':s['font_size_pt_estimate'],
            'limits':['Raster point size is estimated, not recovered source font metadata.','Mask correctness and scientific-content protection require actual visual review.']}
    write(out/'patch.json',report);return report
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('spec');p.add_argument('output_dir');a=p.parse_args();print(json.dumps(patch(a.spec,a.output_dir),ensure_ascii=False))
if __name__=='__main__':main()
