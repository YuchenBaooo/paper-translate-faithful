"""Behavior regressions, using synthetic truth plus portable real-paper fixtures.

Reports stay in invoking workspace/qa-tests; no automatic paper-review claims.
"""
import copy,json,unittest,uuid
from pathlib import Path
from unittest import mock
from reader_common import *
import readerflow as r
import vector_assets as v
import reference_rows as refs
import raster_label_patch as raster
import pdf_compact as compact

FIXTURE=Path(__file__).resolve().parents[1]/'examples'/'cox2021'

class ReaderToolsTests(unittest.TestCase):
    def setUp(self):
        self.root=Path.cwd()/'qa-tests'/('reader-'+uuid.uuid4().hex);self.root.mkdir(parents=True)
        self.plan=read(FIXTURE/'reader-plan.json');self.manifest=read(FIXTURE/'assets.json')

    def bad_plan(self,change,pattern):
        p=copy.deepcopy(self.plan);change(p)
        with self.assertRaisesRegex(InvalidPlan,pattern):r.validate(p,self.manifest)

    def test_portable_contract_and_target_binding(self):
        self.assertTrue(r.validate(self.plan,self.manifest))
        self.bad_plan(lambda p:p['blocks'][3].update(text='漏掉公式与数字。'),'TARGET_TEXT_CHANGED')

    def test_exact_unit_and_asset_occurrences(self):
        self.bad_plan(lambda p:p['blocks'][3]['unit_ids'].append(p['blocks'][3]['unit_ids'][0]),'TARGET_TEXT_CHANGED|UNIT_COVERAGE')
        self.bad_plan(lambda p:p['units'][3]['required_assets'].pop(),'ASSET_OCCURRENCES')
        self.bad_plan(lambda p:p.update(mode_basis=''),'USER_PRIORITY')

    def test_finite_page_dimensions(self):
        self.bad_plan(lambda p:p['page'].update(width=float('nan')),'PAGE_DIMENSIONS')
        self.bad_plan(lambda p:p['page'].update(margins=[1,-1,1,1]),'PAGE_MARGINS')

    def test_unit_asset_binding_and_preserve_reason(self):
        self.bad_plan(lambda p:next(u for u in p['units'] if u['id']=='cox-eq11').update(reason=''),'PRESERVE_REASON')
        self.bad_plan(lambda p:next(u for u in p['units'] if u['id']=='cox-eq11').update(asset='ref-209'),'ASSET_UNIT_NOT_PLACED|ASSET_UNIT_BINDING')
        self.bad_plan(lambda p:next(u for u in p['units'] if u['id']=='cox-laser').update(target=''),'TRANSLATION_TARGET_REQUIRED')

    def test_punctuation_and_empty_math_text(self):
        def atom(t,w=1):return {'text':t,'width':w,'ascent':1,'descent':.2}
        rows=r.wrap([atom(x) for x in '甲乙。）'],3,0,1)
        self.assertEqual(''.join(a.get('text','') for row in rows for a in row['atoms']),'甲乙。）')
        self.assertTrue(all(row['atoms'][0].get('text','x') not in r.CLOSE for row in rows))
        # Math atoms have no text; '' must not behave as closing punctuation.
        rows=r.wrap([atom('甲'),atom('乙'),{'asset':'m','width':2,'ascent':1,'descent':.2}],2,0,1)
        self.assertEqual(len(rows),2);self.assertEqual(rows[1]['atoms'][0]['asset'],'m')
        with self.assertRaisesRegex(InvalidPlan,'ATOM_WIDER'):r.wrap([atom('X',99)],4,0,1)

    def make_source(self):
        p=self.root/'source.pdf';d=fitz.open();pg=d.new_page(width=300,height=300)
        pg.insert_text((20,40),'x = 1',fontsize=10)
        pg.insert_text((20,90),'[1] Author A. Title A.',fontsize=10)
        pg.insert_text((34,105),'Journal A, 2020.',fontsize=10)
        pg.insert_text((20,130),'[2] Author B. Title B.',fontsize=10)
        pg.insert_text((34,145),'Journal B, 2021.',fontsize=10)
        d.set_toc([[1,'Test',1]]);d.save(p);d.close();return p

    def test_vector_composition_glyph_bounds_and_clean_clip_gate(self):
        src=self.make_source();spec={'sources':{'s':{'path':str(src),'sha256':sha(src)}},'assets':[{
            'id':'m','role':'inline_math','composition':'horizontal','source_units':['u'],
            'pieces':[{'source':'s','page':1,'bbox':[19,28,45,44],'baseline':40}], 'evidence':'Known synthetic x = 1 region'}]}
        file=self.root/'spec.json';write(file,spec);result=v.compose(file,self.root/'assets')
        self.assertEqual(result['assets'],1)
        spec['assets'][0]['evidence']='';write(file,spec)
        with self.assertRaisesRegex(InvalidPlan,'CLEAN_CLIP'):v.compose(file,self.root/'reject')
        # This real fixture contains embedded legacy CMEX outline glyphs.
        with fitz.open(FIXTURE/'source-excerpts.pdf') as d:
            page=next(a['page'] for a in self.manifest['assets'] if a['id']=='eR')
            glyphs=v.glyph_bounds(d,page)
        self.assertTrue(any(x.get('ink_bbox') and 'CMEX' in x['font'].upper() for x in glyphs))

    def test_reference_entries_and_continuation(self):
        src=self.make_source();spec={'sources':{'s':{'path':str(src),'sha256':sha(src)}},'expected_numbers':[1,2],
            'regions':[{'source':'s','page':1,'bbox':[10,75,290,93],'evidence':'Known entry starts'},
                       {'source':'s','page':1,'bbox':[10,94,290,155],'evidence':'Known continuation then next entry'}]}
        file=self.root/'refs.json';write(file,spec);out=self.root/'proposal.json';result=refs.extract(file,out)
        self.assertEqual(result['numbers'],[1,2]);self.assertIn('Journal A',read(out)['reference_text']['ref-1'])
        spec['expected_numbers']=[2,1];write(file,spec)
        with self.assertRaisesRegex(InvalidPlan,'REFERENCE_NUMBER_SEQUENCE'):refs.extract(file,out)
        spec['regions']=spec['regions'][1:];write(file,spec)
        with self.assertRaisesRegex(InvalidPlan,'LEADING_CONTINUATION'):refs.extract(file,out)
        spec['regions'][0]['bbox'][1]=100;write(file,spec)
        with self.assertRaisesRegex(InvalidPlan,'PARTIAL_REFERENCE_LINE'):refs.extract(file,out)

    def test_raster_real_mask_and_protection(self):
        result=raster.patch(FIXTURE/'laser-patch.json',self.root/'label')
        self.assertEqual(result['outside_edit_changed_pixels'],0)
        self.assertEqual(result['status'],'candidate-needs-visual-review')
        spec=read(FIXTURE/'laser-patch.json')
        for k in ['source_png','mask_png']:spec[k]=str(FIXTURE/spec[k])
        spec['protected_bboxes_px']=[spec['edit_bbox_px']];file=self.root/'label.json';write(file,spec)
        with self.assertRaisesRegex(InvalidPlan,'MASK_TOUCHES'):raster.patch(file,self.root/'label-reject')

    def test_bounded_compact_timeout_and_start_failure(self):
        src=self.make_source();out=self.root/'timeout.pdf'
        class HangingWorker:
            returncode=None
            def poll(self):return None
            def kill(self):self.killed=True
            def wait(self):self.returncode=-1
        process=HangingWorker()
        with mock.patch.object(compact.subprocess,'Popen',return_value=process):result=compact.compact(src,out,.001)
        self.assertTrue(process.killed);self.assertEqual(result['status'],'unchanged-fallback');self.assertEqual(sha(src),sha(out))
        with mock.patch.object(compact.subprocess,'Popen',side_effect=PermissionError('test denied')):
            result=compact.compact(src,self.root/'start-denied.pdf')
        self.assertEqual(result['issues'][0]['kind'],'worker_start_error')

    def test_actual_compact_pixels_text_and_toc(self):
        src=self.make_source();result=compact.compact(src,self.root/'compact.pdf',30,2)
        self.assertFalse(result['issues']);self.assertTrue(all(p['changed_pixels']==0 and p['text_equal'] for p in result['pages']))

    def test_generic_reader_and_publication_gate(self):
        # Different size, one column and one unit; no Cox counts/styles required.
        src=self.make_source();assets=self.root/'assets';assets.mkdir()
        with fitz.open() as d:
            p=d.new_page(width=12,height=12);p.insert_text((1,8),'x',fontsize=7);d.save(assets/'assets.pdf')
        manifest={'pdf':'assets.pdf','pdf_sha256':sha(assets/'assets.pdf'),'assets':[]};write(assets/'assets.json',manifest)
        plan={'edition':'reader','mode_basis':'Synthetic reflow test','assets_manifest':str(assets/'assets.json'),
            'page':{'width':420,'height':595,'margins':[25,40,25,30],'columns':1},
            'fonts':{'regular':'auto-cjk','bold':'auto-cjk-bold'},'styles':{'body':{'size_pt':10,'bold':False}},
            'units':[{'id':'u','source':'Known fixture 12.5%.','target':'已知测试12.5%。','action':'translate','delivery':'text','required_assets':[]}],
            'blocks':[{'id':'b','kind':'paragraph','unit_ids':['u'],'text':'已知测试12.5%。'}]}
        file=self.root/'plan.json';write(file,plan);job=self.root/'reader';result=r.render(file,job)
        self.assertEqual(result['status'],'pass');self.assertEqual(result['unit_count'],1)
        with self.assertRaisesRegex(InvalidPlan,'REAL_REVIEW_REQUIRED'):r.publish(job,self.root/'denied.pdf')
        review=read(job/'review.json');review.update(reviewer='synthetic-fixture-tester',method='known truth; not real-paper semantic review')
        review['units'][0].update(meaning='pass',numbers='pass',structure='pass',evidence='Exact known synthetic phrase and 12.5%')
        review['visual'].update(status='pass',readability='pass',pages=[1],evidence='Synthetic gate fixture; no real-paper assertion')
        write(job/'review.json',review);r.publish(job,self.root/'published.pdf')
        review['candidate_sha256']='stale';write(job/'review.json',review)
        with self.assertRaisesRegex(InvalidPlan,'REVIEW_STALE'):r.publish(job,self.root/'stale.pdf')
        contract=read(job/'contract.json');original=copy.deepcopy(contract)
        contract['fonts_sha256']['regular']='tampered';write(job/'contract.json',contract)
        with self.assertRaisesRegex(InvalidPlan,'FROZEN_READER_FONT_CHANGED'):r.audit(job)
        write(job/'contract.json',original)
        plan=read(job/'plan.json');plan['mode_basis']='tampered';write(job/'plan.json',plan)
        with self.assertRaisesRegex(InvalidPlan,'FROZEN_READER_INPUT_CHANGED'):r.audit(job)

if __name__=='__main__':unittest.main()
