"""Recover reference entries from explicitly reviewed column regions."""
import argparse,json,re
from pathlib import Path
from reader_common import *

def extract(spec_path,out_path):
    spec_path=Path(spec_path).resolve();s=read(spec_path);docs,manifest=sources(s,spec_path.parent);entries=[];current=None
    try:
        for region in s['regions']:
            require(region.get('evidence','').strip(),'REFERENCE_REGION_EVIDENCE_REQUIRED')
            pg=docs[region['source']][region['page']-1];area=rect(region['bbox']);rows=[]
            for b in pg.get_text('dict',flags=fitz.TEXTFLAGS_DICT&~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
                if b['type']!=0:continue
                for line in b['lines']:
                    lr=rect(line['bbox'])
                    if not area.intersects(lr):continue
                    require(area.contains(lr),'PARTIAL_REFERENCE_LINE: adjust reviewed region boundary')
                    spans=[x for x in line['spans'] if x['text'].strip()]
                    if not spans:continue
                    y=spans[0]['origin'][1];row=next((x for x in rows if abs(x['y']-y)<.15),None)
                    if row:row['spans'].extend(spans)
                    else:rows.append({'y':y,'spans':spans})
            for row in sorted(rows,key=lambda x:x['y']):
                ss=sorted(row['spans'],key=lambda x:x['bbox'][0]);text=''.join(x['text'] for x in ss)
                match=re.match(r'^\s*\[(\d+)\]',text)
                if match:
                    current={'number':int(match[1]),'source_lines':[],'pieces':[]};entries.append(current)
                require(current is not None,'LEADING_CONTINUATION_NEEDS_PREVIOUS_REGION')
                rr=fitz.Rect(ss[0]['bbox'])
                for x in ss[1:]:rr|=fitz.Rect(x['bbox'])
                current['source_lines'].append(text)
                current['pieces'].append({'source':region['source'],'page':region['page'],'bbox':list(rr),'offset_x':rr.x0-area.x0})
        nums=[e['number'] for e in entries]
        require(len(nums)==len(set(nums)),'DUPLICATE_REFERENCE_NUMBER')
        if s.get('expected_numbers') is not None:require(nums==s['expected_numbers'],'REFERENCE_NUMBER_SEQUENCE')
        assets=[{'id':f'ref-{e["number"]}','role':'reference','composition':'vertical','gap_pt':s.get('line_gap_pt',1),
                 'source_units':[f'ref-{e["number"]}'],'pieces':e['pieces'],'evidence':'Reviewed column regions; entry boundaries proposed from native [n] labels; verify author/title/continuations.'} for e in entries]
        output={'version':1,'sources':s['sources'],'assets':assets,'reference_text':{f'ref-{e["number"]}':'\n'.join(e['source_lines']) for e in entries},'review_status':'proposal-not-semantic-validation'}
        # Preserve relative paths when the output spec moves directories.
        for key,source in output['sources'].items():source['path']=manifest[key]['path']
        write(out_path,output);return {'output':str(out_path),'entries':len(entries),'numbers':nums}
    finally:
        for d in docs.values():d.close()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('regions');p.add_argument('output');a=p.parse_args();print(json.dumps(extract(a.regions,a.output),ensure_ascii=False))
if __name__=='__main__':main()
