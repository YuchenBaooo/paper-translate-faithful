"""Synthetic behavior tests. They do not certify real-paper OCR or semantics."""
from pathlib import Path
import uuid
import unittest
import pymupdf as fitz
import paperflow as f
from babeldoc_bridge import OccurrenceLedger
from ocr_region import pixel_to_pdf,extract

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root=Path.cwd()/'qa-tests'/('case-'+uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.src=self.root/'source.pdf';self.job=self.root/'job'
        d=fitz.open();p=d.new_page(width=500,height=600)
        p.insert_text((65,65),'Paper title',fontsize=16,fontname='hebo')
        p.insert_text((65,105),'Abstract',fontsize=12,fontname='hebo')
        p.insert_text((65,140),'This result may improve accuracy by 12.5%.',fontsize=10)
        p.insert_text((65,180),'x = 1.0',fontsize=12,fontname='tiit')
        image=fitz.open();ip=image.new_page(width=240,height=100)
        ip.draw_line((15,75),(225,75),color=(0,0,0));ip.draw_line((15,75),(15,10),color=(0,0,0))
        ip.insert_text((45,35),'Detector',fontsize=10)
        pix=ip.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
        p.insert_image(fitz.Rect(65,210,305,310),stream=pix.tobytes('png'));image.close()
        p.insert_text((65,345),'Figure 1. Setup.',fontsize=10)
        for y in (365,395,425):p.draw_line((65,y),(305,y))
        for x in (65,185,305):p.draw_line((x,365),(x,425))
        p.insert_text((75,385),'Group',fontsize=10)
        p.insert_text((195,385),'Value',fontsize=10)
        p.insert_text((75,415),'Control',fontsize=10)
        p.insert_text((195,415),'1.0',fontsize=10)
        d.save(self.src);d.close()
        f.prepare(self.src,self.job)
        p=f.read(self.job/'plan.json')
        self.targets={'Paper title':'论文题目','Abstract':'摘要',
                      'This result may improve accuracy by 12.5%.':'该结果可能使精度提高12.5%。',
                      'Figure 1. Setup.':'图1. 装置。','Group':'分组','Value':'值','Control':'对照','Detector':'探测器'}
        for u in p['units']:
            if u['source']=='x = 1.0':u.update(action='preserve',role='math',reason='original equation and italic variable')
            elif u['action']=='translate':
                u['role']='title' if u['source']=='Paper title' else 'abstract_label' if u['source']=='Abstract' else 'table_header' if u['source'] in ('Group','Value') else 'table_cell' if u['source']=='Control' else 'body'
                if u['role'] in ('table_header','table_cell'):u.update(table_id='t1',row=0 if u['source'] in ('Group','Value') else 1,col=1 if u['source']=='Value' else 0,rowspan=1,colspan=1)
        p['units'].append({'id':'p1-raster-detector','page':1,'bbox':[108,233,160,249],
                           'span_ids':[],'source':'Detector','action':'translate','role':'figure_label',
                           'kind':'raster_label','size_pt':10,'bold':False,'italic':False,
                           'source_visual_evidence':'Synthetic bitmap label at known coordinates',
                           'background_reviewed':True,'background_rgb':[1,1,1],'protected_tokens':[]})
        p['source_inventory_reviewed']=True;p['glossary']={'accuracy':'精度'}
        p['page_inventory'][0].update(all_visible_content_registered=True,notes='Synthetic known text, bitmap label, formula, grid and all cells enumerated')
        f.write(self.job/'plan.json',p);self.plan=p

    # Preserve fixtures and reports as review evidence in the invoking workspace.

    def accepted(self):
        c=f.freeze(self.job);rows=[{'id':u['id'],'text':self.targets[u['source']]} for u in self.plan['units'] if u['action']=='translate']
        response={'contract_sha256':c['contract_sha256'],'translations':rows}
        f.write(self.root/'response.json',response);f.submit(self.job,self.root/'response.json')
        return response

    def review(self):
        f.review_template(self.job);r=f.read(self.job/'review.json')
        r.update(reviewer='synthetic-fixture-tester',method='known-fixture structural checks, not real-paper review')
        r['extraction'].update(status='pass',pages=[1],evidence='fixture has exactly enumerated objects')
        for row in r['units']:
            row.update(meaning='pass',terminology='pass',numbers='pass',structure='pass',evidence='known synthetic target mapping')
        f.write(self.job/'review.json',r)

    def test_freeze_and_submit(self):
        r=self.accepted();self.assertEqual(len(r['translations']),8)

    def test_duplicate_and_numeric_rejected(self):
        r=self.accepted();r['translations'].append(r['translations'][0])
        f.write(self.root/'bad.json',r)
        with self.assertRaisesRegex(f.Blocked,'IDS'):f.submit(self.job,self.root/'bad.json')
        r['translations'].pop();next(x for x in r['translations'] if '12.5' in x['text'])['text']='精度提高13%。'
        f.write(self.root/'bad.json',r)
        with self.assertRaisesRegex(f.Blocked,'NUMBER_CHANGED'):f.submit(self.job,self.root/'bad.json')

    def test_missing_source_span_and_unknown_role_block(self):
        p=f.read(self.job/'plan.json');p['units'].pop(0);f.write(self.job/'plan.json',p)
        with self.assertRaisesRegex(f.Blocked,'COVERAGE'):f.freeze(self.job)
        p=self.plan;p['units'][0]['role']='unclassified';f.write(self.job/'plan.json',p)
        with self.assertRaisesRegex(f.Blocked,'UNCLASSIFIED'):f.freeze(self.job)

    def test_review_and_stale_plan_block(self):
        self.accepted()
        with self.assertRaises(FileNotFoundError):f.render(self.job)
        self.review();self.plan['glossary']['accuracy']='准确性';f.write(self.job/'plan.json',self.plan)
        with self.assertRaisesRegex(f.Blocked,'PLAN_CHANGED'):f.render(self.job)

    def test_native_roundtrip_and_publish_gate(self):
        self.accepted();self.review();result=f.render(self.job)
        report=f.audit(self.job)
        self.assertEqual([x['code'] for x in report['issues']],['REVIEW_GATE'],report)
        with self.assertRaisesRegex(f.Blocked,'DELIVERY_GATE_FAILED'):f.publish(self.job,self.root/'output.pdf')
        r=f.read(self.job/'review.json');r['visual'].update(candidate_sha256=result['sha256'],status='pass',pages=[1],evidence='synthetic expected region, formula/grid and bitmap nontext pixel checks')
        f.write(self.job/'review.json',r)
        report=f.audit(self.job);self.assertEqual(report['status'],'pass',report)
        out=self.root/'output.pdf';f.publish(self.job,out);self.assertTrue(out.is_file())
        with self.assertRaisesRegex(f.Blocked,'OUTPUT_EXISTS'):f.publish(self.job,out)

    def test_font_conflict_never_shrinks(self):
        self.plan['typography']['roles']['body']['size_pt']=70
        f.write(self.job/'plan.json',self.plan);self.accepted();self.review()
        with self.assertRaisesRegex(f.Blocked,'FONT_LAYOUT_CONFLICT'):f.render(self.job)
        self.assertFalse((self.job/'candidate.pdf').exists())

    def test_changed_protected_formula_blocks(self):
        self.accepted();self.review();f.render(self.job)
        d=fitz.open(self.job/'candidate.pdf');d[0].draw_rect(fitz.Rect(65,169,100,182),fill=(1,1,1),color=None)
        p=self.job/'changed.pdf';d.save(p);d.close();f.stage(self.job,p)
        report=f.audit(self.job)
        self.assertIn('PROTECTED_REGION_CHANGED',[x['code'] for x in report['issues']])

    def test_leading_decimal_and_formula_ledger(self):
        r=self.accepted();c=f.contract(self.job)
        # A deliberately changed source/target pair exercises the numerical
        # comparator directly, with no fixture layout or review claims.
        u=next(u for u in c['plan']['units'] if u['action']=='translate')
        c['plan']['units']=[dict(u,source='The value is .5 m.')]
        with self.assertRaisesRegex(f.Blocked,'NUMBER_CHANGED'):
            f.validate_translations(c,[{'id':u['id'],'text':'数值为5 m。'}])
        f.validate_translations(c,[{'id':u['id'],'text':'数值为.5 m。'}])
        capture=OccurrenceLedger();capture.process('same');capture.process('same')
        self.assertEqual(len(capture.rows),2)
        rows=[{'id':'segment-000000','source':'ZXQFORM1QXZ value','text':'ZXQFORM1QXZ 值'}]
        replay=OccurrenceLedger(rows);self.assertEqual(replay.process('ZXQFORM1QXZ value'),'ZXQFORM1QXZ 值');replay.complete()
        rows[0]['text']='值'
        with self.assertRaisesRegex(f.Blocked,'FORMULA_TOKEN_CHANGED'):
            OccurrenceLedger(rows).process('ZXQFORM1QXZ value')
        with self.assertRaisesRegex(f.Blocked,'SEQUENCE_CHANGED'):
            OccurrenceLedger(rows).process('different')

    def test_table_metadata_and_formula_overlap(self):
        p=f.deepcopy(self.plan);u=next(u for u in p['units'] if u['role']=='table_header');u.pop('table_id')
        f.write(self.job/'plan.json',p)
        with self.assertRaisesRegex(f.Blocked,'TABLE_ID'):f.freeze(self.job)
        p=f.deepcopy(self.plan);u=next(u for u in p['units'] if u['role']=='figure_label')
        p['protected'].append({'id':'math-mask','page':1,'bbox':u['bbox'],'kind':'formula','allow_label_edits':True})
        f.write(self.job/'plan.json',p)
        with self.assertRaisesRegex(f.Blocked,'PROTECTED_OBJECT'):f.freeze(self.job)

    def test_ocr_crop_mapping(self):
        self.assertEqual(pixel_to_pdf([10,20,110,120],100,50,200,100,144),[60,45,160,95])
        result=extract(self.src,1,[65.1,210.2,305,310],self.root/'ocr-crop',300)
        proposal=f.read(self.root/'ocr-crop/ocr-proposal.json')
        self.assertEqual(result['status'],'unreviewed');self.assertEqual(proposal['words'],[])
        self.assertTrue((self.root/'ocr-crop/region.png').is_file())

if __name__=='__main__':unittest.main(verbosity=2)
