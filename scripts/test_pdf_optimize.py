"""Structural performance fixes must preserve pixels, text and navigation."""
import unittest,uuid
from pathlib import Path
from unittest import mock
import pdf_optimize as p

class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.root=Path.cwd()/'qa-tests'/('performance-'+uuid.uuid4().hex);self.root.mkdir(parents=True)
        source=p.fitz.open();pg=source.new_page(width=300,height=300)
        pg.insert_text((25,40),'x = 1',fontsize=12)
        for y in range(70,290,15):pg.insert_text((25,y),'Clipped source text should not be rendered',fontsize=8)
        source.save(self.root/'asset.pdf')
        d=p.fitz.open();pg=d.new_page(width=500,height=700)
        pg.show_pdf_page(p.fitz.Rect(50,50,120,75),source,0,clip=p.fitz.Rect(20,25,90,50),keep_proportion=False)
        for j in range(8):pg.insert_text((50,120+j*15),'Visible target sentence '+str(j),fontsize=10)
        pg.insert_link({'kind':p.fitz.LINK_URI,'from':p.fitz.Rect(50,120,200,135),'uri':'https://example.org/'})
        d.set_toc([[1,'Example',1]]);d.set_metadata({'title':'Exact vector test'})
        self.source=self.root/'source.pdf';d.save(self.source);d.close();source.close()

    def test_full_roundtrip_vector_text_pixels_and_bookmarks(self):
        out=self.root/'optimized.pdf';result=p.optimize(self.source,out,30,(2,3))
        self.assertEqual(result['status'],'equivalent-vector-output');self.assertEqual(result['after']['content_streams'],1)
        report=p.read(out.with_suffix('.performance.json'));self.assertEqual(report['verification']['status'],'pass')
        with p.fitz.open(out) as d:self.assertEqual(len(d[0].get_images()),0);self.assertIn('x = 1',d[0].get_text())
        self.assertGreaterEqual(result['pruned_forms'],1)

    def test_unsafe_form_rewrite_is_rolled_back(self):
        candidate=self.root/'rollback.pdf';report=self.root/'report.json'
        def bad(doc,x):doc.update_stream(x,b'')
        with mock.patch.object(p,'replace_form_with_clipped',side_effect=bad):p.worker(self.source,candidate,report,[2,3])
        r=p.read(report);self.assertTrue(any(x['status']=='kept' and x['reason']=='native_text_changed' for x in r['forms']))
        self.assertEqual(p.verify(self.source,candidate,[2,3])['status'],'pass')

    def test_pixel_and_text_tampering_is_rejected(self):
        bad=self.root/'bad.pdf'
        with p.fitz.open(self.source) as d:
            d[0].insert_text((50,400),'Unexpected content',fontsize=10);d.save(bad)
        report=p.verify(self.source,bad,[2]);self.assertEqual(report['status'],'fail')
        self.assertTrue(any(x['kind']=='pixels_changed' for x in report['issues']))

    def test_existing_output_and_source_never_overwritten(self):
        before=p.sha(self.source)
        with self.assertRaisesRegex(p.InvalidPlan,'SOURCE_OR_OUTPUT_INVALID'):p.optimize(self.source,self.source)
        out=self.root/'exists.pdf';out.write_bytes(b'keep')
        with self.assertRaisesRegex(p.InvalidPlan,'SOURCE_OR_OUTPUT_INVALID'):p.optimize(self.source,out)
        self.assertEqual(out.read_bytes(),b'keep');self.assertEqual(p.sha(self.source),before)

    def test_rotated_page_requires_explicit_adapter(self):
        rotated=self.root/'rotated.pdf'
        with p.fitz.open(self.source) as d:d[0].set_rotation(90);d.save(rotated)
        with self.assertRaisesRegex(p.InvalidPlan,'ROTATED_PAGES_REQUIRE_ADAPTER'):p.worker(rotated,self.root/'reject.pdf',self.root/'reject.json',[2])

if __name__=='__main__':unittest.main()
