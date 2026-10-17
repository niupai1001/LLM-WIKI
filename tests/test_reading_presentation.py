import unittest
from station import reading_presentation as p
class ReadingPresentationTests(unittest.TestCase):
    def test_inserts_headings_without_rewriting_original_characters(self):
        body='# Article\n\nOriginal first paragraph.\n\nSecond paragraph.\n'
        plan={'sections':[{'paragraph_index':1,'title':'研究问题'},{'paragraph_index':2,'title':'机制解释'}]}
        formatted=p.format_body(body,plan)
        self.assertEqual(formatted.replace('## 研究问题\n\n','').replace('## 机制解释\n\n',''),body)
    def test_existing_sections_unchanged(self):
        body='# Title\n\n## Existing\n\nBody'
        self.assertEqual(p.format_body(body,{'sections':[]}),body)
        with self.assertRaises(ValueError):p.format_body(body,{'sections':[{'paragraph_index':2,'title':'重复章节'}]})
    def test_invalid_or_rewritten_input_is_rejected(self):
        body='# Title\n\nFirst\n\nSecond'
        for plan in [{'sections':[{'paragraph_index':9,'title':'问题介绍'}]},
                     {'sections':[{'paragraph_index':1,'title':'<script>'}]},
                     {'sections':[{'paragraph_index':2,'title':'缺少首段'}]},
                     {'sections':[{'paragraph_index':1,'title':'介绍内容','body':'new facts'}]},
                     {'sections':[{'paragraph_index':1,'title':'介绍内容'},{'paragraph_index':1,'title':'重复段落'}]}]:
            with self.assertRaises(ValueError):p.format_body(body,plan)
    def test_library_and_version_bound_layout_falls_back_when_stale(self):
        import tempfile,json
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            ws=SimpleNamespace(root=Path(temp),library_id='preview')
            body='# Title\n\nOriginal paragraph.'
            plan={'sections':[{'paragraph_index':1,'title':'研究问题'}]}
            record=p.record('preview','paper',body,plan)
            folder=ws.root/'presentations';folder.mkdir();target=folder/'paper.json';target.write_text(json.dumps(record))
            self.assertIn('## 研究问题',p.display_body(ws,'paper',body))
            self.assertEqual(p.display_body(ws,'paper',body+'Changed'),body+'Changed')
            record['library_id']='other';target.write_text(json.dumps(record))
            self.assertEqual(p.display_body(ws,'paper',body),body)
    def test_generated_reference_heading_does_not_hide_missing_reading_sections(self):
        self.assertFalse(p.has_sections('# Title\n\nBody\n\n## 原始证据\n\nReference'))
        self.assertTrue(p.has_sections('# Title\n\n## 研究问题\n\nBody'))
