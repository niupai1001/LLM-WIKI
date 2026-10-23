import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import citations, web, fact_checks


A = '1111111111111111'
B = '2222222222222222'
C = '3333333333333333'


class CitationTests(unittest.TestCase):
    def test_reader_gets_explanation_before_evidence_without_numbering_hint(self):
        body = '## 工具发现\n\n先读取能力描述，再选择工具。\n\n## 执行反馈\n\n执行结果用于决定是否修正参数。'
        with patch('station.citations.source_info', self.sources.get):
            rendered = web.md_render(body, [A, B])
        self.assertNotIn('编号对应正文引用', rendered)
        self.assertNotIn('citation-navigation', rendered)
        self.assertLess(rendered.index('执行结果用于决定是否修正参数'), rendered.index('evidence-panel'))
        self.assertNotIn('<details', rendered)

    def test_publisher_dates_preserve_declared_precision(self):
        self.assertEqual(citations._date('2024'), '2024')
        self.assertEqual(citations._date('2024/07'), '2024-07')
        self.assertEqual(citations._date('2024/07/16'), '2024-07-16')
        self.assertEqual(citations._date('2024-13'), '')
        self.assertEqual(citations._date('0000'), '')

    def setUp(self):
        self.sources = {
            A: {'title': 'Plan-and-Patch: Diffusion Language Models for Agentic Planning', 'published': 'Fri, 09 Oct 2026 00:00:00 -0400', 'url': 'https://arxiv.org/abs/2610.10786', 'pdf_url': 'https://arxiv.org/pdf/2610.10786'},
            B: {'title': 'On the Clock: Towards Punctual and Productive Time-Budgeted AI Agents', 'published': '2026-10-08T12:00:00+00:00', 'url': 'https://arxiv.org/abs/2610.10833'},
            C: {'title': 'NOMOS: Compiling Written Policies into Verified Tool-Call Gates', 'published': '', 'url': 'https://arxiv.org/abs/2610.11030'},
        }

    def test_first_body_occurrence_controls_numbering_repeats_are_stable(self):
        body = f'B first [来源 222222](/source/{B}). A second [S:{A}]. B again [Named source](/source/{B}).'
        rendered, entries = citations.prepare_citations(body, [A, B, C, C], self.sources.get)
        self.assertEqual([entry.source_id for entry in entries], [B, A, C])
        self.assertEqual(rendered.count('[1 · On the Clock]'), 2)
        self.assertIn('[2 · Plan-and-Patch]', rendered)
        self.assertIn(f'href="/source/{B}"', rendered)
        self.assertIn('Towards Punctual and Productive', rendered)
        self.assertIn('发布日期 2026-10-08', rendered)
        evidence = citations.evidence_html(entries, 'test-page')
        self.assertIn('id="evidence-test-page-1"', evidence)
        self.assertIn('原文链接', evidence)
        self.assertIn('发布日期 <time datetime="2026-10-09">2026-10-09</time>', evidence)
        self.assertNotIn('来源ID', evidence)
        self.assertNotIn(f'>{A}<', evidence)

    def test_legacy_reference_section_removed_without_losing_other_sections(self):
        legacy = f'# Page\n\nConclusion [来源 a](/source/{A}).\n\n---\n\n## 原始证据\n\n- [Plan-and-Patch](/source/{A}) · 来源ID `{A}`\n\n## Further work\n\nKeep this section.\n'
        rendered, entries = citations.prepare_citations(legacy, [A], self.sources.get)
        self.assertNotIn('## 原始证据', rendered)
        self.assertNotIn('来源ID', rendered)
        self.assertIn('## Further work', rendered)
        self.assertIn('Keep this section.', rendered)
        self.assertEqual(len(entries), 1)
        self.assertEqual(citations.evidence_html(entries).count('<h2>原始证据'), 1)

    def test_narrative_evidence_section_and_code_are_preserved(self):
        code = f'`[S:{A}]`\n\n```md\n## 原始证据\n- [Do not transform](/source/{A})\n```\n\n## 原始证据\n\nThis is discussion, not a reference list.\n'
        rendered, entries = citations.prepare_citations(code, [], self.sources.get)
        self.assertEqual(entries, [])
        self.assertEqual(rendered, code.rstrip())

    def test_unknown_sources_and_malicious_labels_are_not_html(self):
        unknown = 'ffffffffffffffff'
        self.sources[A]['title'] = '<img src=x onerror="alert(1)">: malicious title'
        self.sources[A]['pdf_url'] = 'javascript:alert(1)'
        self.sources[A]['url'] = 'https://user:secret@example.org/private'
        body = f'[<script>alert(1)</script>](/source/{A}) [S:{unknown}]'
        with patch.object(citations, 'source_info', side_effect=self.sources.get):
            rendered = web.md_render(body, [A, unknown])
        self.assertNotIn('<script', rendered)
        self.assertNotIn('<img', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('user:secret', rendered)
        self.assertIn('&lt;img', rendered)
        self.assertIn('来源记录缺失', rendered)
        self.assertIn(f'/source/{unknown}', rendered)
        self.assertNotIn('onclick=', rendered)

    def test_original_markdown_does_not_gain_citation_rewriting(self):
        body = f'Original label [来源 original](/source/{A})'
        original = web.md_render(body)
        self.assertIn('来源 original</a>', original)
        self.assertNotIn('source-citation', original)
        self.assertNotIn('evidence-panel', original)

    def test_sanitizer_keeps_only_generated_citation_class_and_safe_attributes(self):
        body = f'[S:{A}] <a href="/safe" onclick="alert(1)" style="display:none" class="other">safe</a>'
        with patch.object(citations, 'source_info', side_effect=self.sources.get):
            rendered = web.md_render(body, [A], evidence_prefix='safe')
        self.assertIn('class="source-citation"', rendered)
        self.assertIn('title="[1] Plan-and-Patch:', rendered)
        self.assertNotIn('onclick=', rendered)
        self.assertNotIn('style=', rendered)
        self.assertNotIn('class="other"', rendered)
        self.assertIn('id="evidence-safe"', rendered)

    def test_short_title_limits_and_invalid_identifiers(self):
        title = 'A very long descriptive paper title that contains many words before a colon: subtitle'
        self.assertLessEqual(len(citations.short_title(title)), 35)
        rendered, entries = citations.prepare_citations('[S:../../secrets]', ['../../secrets', '<script>', A], self.sources.get)
        self.assertEqual([entry.source_id for entry in entries], [A])
        self.assertNotIn('class="source-citation"', rendered)

    def test_page_and_draft_render_one_evidence_list_and_safe_writing_issues(self):
        body = f'## Finding\n\nEvidence [S:{A}].\n\n---\n\n## 原始证据\n\n- [Legacy title](/source/{A}) · 来源ID `{A}`'
        page_data = {'slug': 'example', 'title': 'Example', 'category': 'other', 'page_role': 'paper', 'source_ids': [A], 'related_slugs': [], 'body': body}
        with tempfile.TemporaryDirectory() as temp, patch.object(fact_checks, 'WIKI', Path(temp)), patch.object(fact_checks, 'META', Path(temp)), patch.object(fact_checks, 'REVIEWS', Path(temp)):
            (Path(temp) / 'example.md').write_text(body)
            with patch.object(web, 'wiki_pages', return_value=[page_data]), patch.object(citations, 'source_info', side_effect=self.sources.get):
                page = web.page('example').body.decode()
        self.assertEqual(page.count('<h2>原始证据'), 1)
        self.assertIn('[1 · Plan-and-Patch]', page)
        self.assertNotIn('证据与可信边界', page)
        self.assertIn('断言与原文证据', page)
        self.assertIn('<span class="tag subtle">论文解读</span>', page)
        self.assertNotIn('<span class="tag subtle">概念条目</span>', page)
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp)
            (pending / 'draft.json').write_text('{}')
            proposal = {'source_id': A, 'title': 'Draft', 'source_ids': [A], 'category': 'other', 'action': 'create', 'body_markdown': body,
                        'writing_issues': ['正文可能偏长', '<script>alert(1)</script>']}
            with patch.object(web, 'PENDING', pending), patch.object(web, 'load_json', return_value=proposal), patch.object(web, 'connect'), patch.object(citations, 'source_info', side_effect=self.sources.get):
                draft = web.drafts().body.decode()
        self.assertEqual(draft.count('<h2>原始证据'), 1)
        self.assertNotIn('证据与可信边界', draft)
        self.assertIn('原始资料', draft)
        self.assertNotIn('class="review-evidence"', draft)
        self.assertIn('编写检查提示', draft)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', draft)

    def test_home_groups_topics_and_concepts_and_preserves_filters(self):
        pages = [
            {'slug': 'topic', 'title': 'Topic guide', 'page_kind': 'topic', 'category': 'other', 'source_ids': [], 'body': 'Topic match'},
            {'slug': 'concept', 'title': 'Concept detail', 'page_kind': 'concept', 'category': 'other', 'source_ids': [], 'body': 'Concept match'},
            {'slug': 'paper', 'title': 'Paper reading', 'page_kind': 'concept', 'page_role': 'paper', 'category': 'other', 'source_ids': [], 'body': 'Paper match'},
            {'slug': 'comparison', 'title': 'Comparison detail', 'page_kind': 'concept', 'page_role': 'comparison', 'category': 'other', 'source_ids': [], 'body': 'Comparison match'},
            {'slug': 'legacy', 'title': 'Legacy knowledge', 'category': 'other', 'source_ids': [], 'body': 'Legacy excluded'},
        ]
        with tempfile.TemporaryDirectory() as temp, patch.object(web, 'wiki_pages', return_value=pages), patch.object(web, 'PENDING', Path(temp)), patch.object(web, 'connect') as connection:
            connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = [0]
            rendered = web.home().body.decode()
            self.assertIn('主题入口', rendered)
            self.assertIn('概念知识', rendered)
            self.assertIn('论文解读', rendered)
            self.assertIn('研究比较', rendered)
            self.assertLess(rendered.index('href="/page/topic"'), rendered.index('href="/page/concept"'))
            self.assertLess(rendered.index('href="/page/concept"'), rendered.index('<h2>论文解读'))
            self.assertLess(rendered.index('<h2>论文解读'), rendered.index('href="/page/paper"'))
            self.assertLess(rendered.index('href="/page/paper"'), rendered.index('<h2>研究比较'))
            self.assertLess(rendered.index('<h2>研究比较'), rendered.index('href="/page/comparison"'))
            self.assertIn('href="/page/legacy"', rendered)
            filtered = web.home(q='match', category='other').body.decode()
            self.assertIn('href="/page/topic"', filtered)
            self.assertIn('href="/page/concept"', filtered)
            self.assertIn('href="/page/paper"', filtered)
            self.assertIn('href="/page/comparison"', filtered)
            self.assertNotIn('href="/page/legacy"', filtered)

    def test_published_history_uses_stable_journal_ids_and_preserves_old_anchors(self):
        records = {
            'paper-event-one': {'slug': 'paper-reading', 'title': 'Paper reading', 'source_ids': [A],
                                'publication': {'at': '2026-10-15T02:00:00Z'}},
            'concept-event-one': {'slug': 'shared-concept', 'title': 'First synthesis', 'source_ids': [A, B],
                                  'updated_at': '2026-10-15T03:00:00Z'},
            'concept-event-two': {'slug': 'shared-concept', 'title': 'Second synthesis', 'source_ids': [A, B, C],
                                  'publication': {'at': '2026-10-15T04:00:00Z'}},
            'old-proposal-file': {'slug': 'old-proposal', 'title': 'Old proposal', 'proposal_id': 'p_old',
                                  'source_id': A, 'handled_at': '2026-10-15T01:00:00Z'},
            'old-source-file': {'slug': 'old-source', 'title': 'Old source', 'source_id': B,
                                'handled_at': '2026-10-15T00:00:00Z'},
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pending = root / 'pending'
            pending.mkdir()
            history = root / 'proposal_history'
            history.mkdir()
            for name, record in records.items():
                (history / (name + '.json')).write_text(json.dumps({**record, 'outcome': 'approved'}))
            (history / 'rejected.json').write_text(json.dumps({'outcome': 'rejected'}))
            with patch.object(web, 'PENDING', pending), patch.object(web, 'connect'), patch.object(web, 'wiki_pages', return_value=[]):
                rendered = web.drafts().body.decode()
                again = web.drafts().body.decode()
        expected = ['draft-concept-event-two', 'draft-concept-event-one', 'draft-paper-event-one',
                    'draft-p_old', 'draft-' + B]
        self.assertEqual(re.findall(r'class="wiki-card" id="([^"]+)"', rendered), expected)
        self.assertEqual(re.findall(r'class="wiki-card" id="([^"]+)"', again), expected)
        self.assertEqual(rendered.count('href="/page/shared-concept"'), 2)
        self.assertIn('2026-10-15 10:00', rendered)
        self.assertNotIn('draft-rejected', rendered)



if __name__ == '__main__':
    unittest.main()
