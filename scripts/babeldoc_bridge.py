"""Optional local BabelDOC capture/replay adapter; no translation provider.

Capture every occurrence without deduplication. Replay must match the same call
sequence exactly, or fail. Outputs remain drafts requiring paperflow stage/audit.
Actual SDK/asset setup is optional and needs separate runtime validation.
"""
import argparse
from collections import Counter
import inspect
import json
from pathlib import Path
import re
import threading
import paperflow as f

TOKEN=re.compile(r'ZXQFORM[0-9]+QXZ')

class OccurrenceLedger:
    def __init__(self,rows=None):
        self.rows=[] if rows is None else rows
        self.capture=rows is None;self.cursor=0;self.lock=threading.Lock()
    def process(self,source):
        with self.lock:
            i=self.cursor;self.cursor+=1
            if self.capture:
                self.rows.append({'id':f'segment-{i:06d}','source':source,'text':'','unit_ids':[]})
                return source
            f.require(i<len(self.rows),'BABELDOC_UNCAPTURED_OCCURRENCE')
            row=self.rows[i]
            f.require(row['id']==f'segment-{i:06d}' and row['source']==source,'BABELDOC_SEGMENT_SEQUENCE_CHANGED')
            f.require(isinstance(row.get('text'),str) and bool(row['text'].strip()),'BABELDOC_TRANSLATION_MISSING')
            f.require(Counter(TOKEN.findall(source))==Counter(TOKEN.findall(row['text'])),'BABELDOC_FORMULA_TOKEN_CHANGED')
            return row['text']
    def complete(self):
        f.require(self.capture or self.cursor==len(self.rows),'BABELDOC_UNUSED_CAPTURED_SEGMENTS')

def run(job,mode,reviews=None,allow_assets=False):
    job=Path(job).resolve();c=f.contract(job)
    f.require(allow_assets,'ASSET_SETUP_CONSENT_REQUIRED: BabelDOC can fetch model/font assets; explicit --allow-model-assets required')
    try:
        from babeldoc.translator.translator import BaseTranslator,set_translate_rate_limiter
        from babeldoc.docvision.doclayout import DocLayoutModel
        from babeldoc.format.pdf.high_level import translate
        from babeldoc.format.pdf.translation_config import TranslationConfig,WatermarkOutputMode
    except ImportError as e:raise f.Blocked('BABELDOC_NOT_INSTALLED_IN_THIS_PYTHON') from e
    capture_path=job/'babeldoc-segments.json'
    if mode=='capture':
        f.require(not capture_path.exists(),'CAPTURE_EXISTS: do not replace segment identity')
        ledger=OccurrenceLedger()
    else:
        _,t=f.state(job);f.review_gate(job,c,t)
        data=f.read(reviews);old=f.read(capture_path)
        f.require(data.get('source_sha256')==c['source_sha256'] and data.get('contract_sha256')==c['contract_sha256'],'BABELDOC_REVIEW_CONTRACT_CHANGED')
        f.require(data.get('capture_sha256')==f.digest(old['segments']),'BABELDOC_CAPTURE_HASH_CHANGED')
        f.require(len(data['segments'])==len(old['segments']),'BABELDOC_REVIEW_COVERAGE')
        translated={r['id']:r['text'] for r in t['translations']}
        targets={u['id']:translated.get(u['id'],u['source']) for u in c['plan']['units']};seen=[]
        for row,original in zip(data['segments'],old['segments']):
            f.require(row['id']==original['id'] and row['source']==original['source'],'BABELDOC_SOURCE_SEGMENT_CHANGED')
            ids=row.get('unit_ids',[]);f.require(ids and all(i in targets for i in ids),'BABELDOC_UNIT_BINDING_MISSING')
            # Bind final wording to the already reviewed job translation. For
            # mixed formula segments keep placeholders exact and map prose IDs.
            prose=TOKEN.sub('',row['text'])
            expected=''.join(targets[i] for i in ids)
            f.require(f.normalize(prose)==f.normalize(expected),'BABELDOC_TARGET_NOT_BOUND_TO_REVIEWED_UNITS')
            seen.extend(i for i in ids if i in translated)
        f.require(Counter(seen)==Counter(translated.keys()),'BABELDOC_UNIT_BINDING_COVERAGE')
        ledger=OccurrenceLedger(data['segments'])
    class Translator(BaseTranslator):
        name='paper-faithful-occurrences';model='active-chat-reviewed'
        def __init__(self):super().__init__('en','zh',ignore_cache=True)
        def get_formular_placeholder(self,placeholder_id):
            token=f'ZXQFORM{placeholder_id}QXZ';return token,re.escape(token)
        def do_translate(self,text,rate_limit_params=None):return ledger.process(text)
        def do_llm_translate(self,text,rate_limit_params=None):
            raise f.Blocked('BABELDOC_LLM_PATH_UNSUPPORTED: use classic local callback route')
    opts={'translator':Translator(),'input_file':job/'source.pdf','lang_in':'en','lang_out':'zh',
          'doc_layout_model':DocLayoutModel.load_onnx(),'output_dir':job/'babeldoc-work',
          'no_dual':True,'no_mono':False,'qps':1000,'pool_max_workers':1,
          'skip_scanned_detection':False,'disable_rich_text_translate':True,
          'watermark_output_mode':WatermarkOutputMode.NoWatermark,
          'auto_extract_glossary':False,'save_auto_extracted_glossary':False,
          'enable_graphic_element_process':True,'remove_non_formula_lines':False}
    sig=inspect.signature(TranslationConfig)
    unsupported=[k for k in opts if k not in sig.parameters and not any(p.kind==p.VAR_KEYWORD for p in sig.parameters.values())]
    f.require(not unsupported,'BABELDOC_API_CHANGED:'+','.join(unsupported))
    set_translate_rate_limiter(1000);result=translate(TranslationConfig(**opts));ledger.complete()
    generated=getattr(result,'no_watermark_mono_pdf_path',None) or getattr(result,'mono_pdf_path',None)
    f.require(bool(generated) and Path(generated).is_file(),'BABELDOC_NO_OUTPUT')
    if mode=='capture':
        f.write(capture_path,{'contract_sha256':c['contract_sha256'],'source_sha256':c['source_sha256'],
                              'capture_sha256':f.digest(ledger.rows),'segments':ledger.rows})
        return {'segments':str(capture_path),'count':len(ledger.rows),'layout_probe':str(generated),
                'status':'captured-untranslated-not-deliverable'}
    return {'candidate':str(generated),'status':'draft-needs-font-region-and-visual-audit',
            'next':'paperflow.py stage JOB CANDIDATE.pdf then audit; never publish this path directly'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['capture','render']);p.add_argument('job')
    p.add_argument('--reviews');p.add_argument('--allow-model-assets',action='store_true');a=p.parse_args()
    try:
        f.require(a.mode=='capture' or a.reviews,'--reviews is required for render')
        print(json.dumps(run(a.job,a.mode,a.reviews,a.allow_model_assets),ensure_ascii=False,indent=2));return 0
    except (f.Blocked,FileNotFoundError) as e:print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
