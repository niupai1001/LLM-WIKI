"""PDF-paper import controls and errors, without external requests or live data."""
import asyncio
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from fixture_config import category_fixture
from station import collector, common, web
from station.workspaces import Workspace, use_workspace


HTML = b'<html><head><title>Website article</title></head><body><article>Ordinary website text.</article></body></html>'
TEXT = 'The archived PDF contains the research method, conditions and results. ' * 3


def pdf_bytes(readable=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    if readable:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 20 300 Td (' + TEXT + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PaperImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = Workspace('pdf-papers', self.root / 'pdf-papers')
        self.old = Workspace('old-compatible', self.root / 'old-compatible')
        fixture = category_fixture()
        fixture.start()
        self.addCleanup(fixture.stop)
        for ws in (self.ws, self.old):
            with use_workspace(ws):
                common.connect().close()
        common.save_json(self.ws.root / 'paper-workflow.json', {'enabled': True})

    def assert_no_archive(self):
        self.assertEqual(list(self.ws.raw.glob('*.json')), [])
        self.assertEqual(list(self.ws.originals.iterdir()), [])
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)

    def test_pdf_policy_upload_form_only_offers_pdf_papers(self):
        with use_workspace(self.ws):
            rendered = web.sources().body.decode()
        form = rendered.split('id="import"', 1)[1]
        self.assertIn('accept=".pdf"', form)
        self.assertIn('type="hidden" name="kind" value="paper"', form)
        self.assertIn('论文 PDF 地址或 arXiv 链接', form)
        self.assertNotIn('.txt', form)
        self.assertNotIn('.md', form)
        self.assertNotIn('<option value="document">', form)
        self.assertNotIn('<option value="note">', form)
        self.assertNotIn('公开网页提取正文', form)

    def test_library_without_policy_keeps_original_form_and_html_import(self):
        with use_workspace(self.old):
            form = web.sources().body.decode().split('id="import"', 1)[1]
            self.assertIn('accept=".pdf,.md,.txt"', form)
            self.assertIn('<option value="document">', form)
            self.assertIn('<option value="note">', form)
            self.assertIn('公开网页提取正文', form)
            with patch.object(collector, 'fetch_bytes', return_value=HTML), patch.object(collector, '_html_text', return_value=TEXT):
                sid = collector.import_url('https://example.org/legacy-article', 'research')
            record = common.load_json(self.old.raw / (sid + '.json'))
        self.assertEqual(record['kind'], 'article')
        self.assertEqual(record['content_format'], 'html')
        self.assertEqual((self.old.originals / record['asset_path']).read_bytes(), HTML)

    def test_non_pdf_link_has_clear_400_and_leaves_no_raw_or_html_asset(self):
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', return_value=HTML), \
                patch.object(collector, '_html_text', return_value=TEXT), \
                patch.object(collector, 'refresh_bibliographies') as metadata:
            with self.assertRaises(HTTPException) as failure:
                asyncio.run(web.operation(collector.import_url, 'https://example.org/article',
                                          category='research', verify_metadata=True))
            metadata.assert_not_called()
        self.assertEqual(failure.exception.status_code, 400)
        self.assertIn('未取得合格的论文 PDF 原件', failure.exception.detail)
        self.assert_no_archive()

    def test_arxiv_html_fallback_without_pdf_is_not_admitted_and_is_removed(self):
        html = b'<html><body class="ltx_document"><section class="ltx_section">A full HTML paper.</section></body></html>'
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', side_effect=[b'<html>Rate limited</html>', html]), \
                patch.object(collector, '_html_text', return_value=TEXT):
            with self.assertRaisesRegex(ValueError, 'PDF 原件'):
                collector.import_url('https://arxiv.org/abs/2401.12345', 'research')
        self.assert_no_archive()

    def test_failed_website_is_not_reported_as_a_successful_duplicate(self):
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', side_effect=OSError('Unavailable website')):
            with self.assertRaisesRegex(ValueError, '未取得合格的论文 PDF 原件'):
                collector.import_url('https://example.org/unavailable-paper', 'research')
        self.assert_no_archive()

    def test_manual_real_pdf_link_is_classified_as_paper_and_remains_idempotent(self):
        data = pdf_bytes()
        url = 'https://example.org/research.pdf'
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', return_value=data) as fetch:
            sid = collector.import_url(url, 'research')
            self.assertEqual(collector.import_url(url, 'research'), sid)
            fetch.assert_called_once_with(url)
            record = common.load_json(self.ws.raw / (sid + '.json'))
        self.assertEqual(record['kind'], 'paper')
        self.assertEqual(record['content_status'], 'full-text')
        self.assertEqual((self.ws.originals / record['pdf_path']).read_bytes(), data)
        self.assertEqual([path.name for path in self.ws.originals.iterdir()], [sid + '.pdf'])

    def test_existing_scanned_pdf_does_not_refresh_to_html_on_repeat_import(self):
        url = 'https://example.org/scanned.pdf'
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', return_value=pdf_bytes(readable=False)):
            sid = collector.import_url(url, 'research')
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes') as fetch, \
                patch.object(collector, 'refresh_source') as refresh:
            self.assertEqual(collector.import_url(url, 'research'), sid)
            fetch.assert_not_called()
            refresh.assert_not_called()
        self.assertEqual(common.load_json(self.ws.raw / (sid + '.json'))['content_status'], 'missing')

    def test_feed_news_still_returns_early_without_fetching(self):
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes') as fetch:
            db = common.connect()
            try:
                self.assertIsNone(collector.archive({'kind': 'news', 'url': 'https://example.org/news',
                                                     'title': 'News', 'category': 'research'}, db))
            finally:
                db.close()
            fetch.assert_not_called()
        self.assert_no_archive()

    def test_pdf_workflow_hides_second_pass_and_excludes_paper_reading_targets(self):
        with use_workspace(self.ws), patch.object(collector, 'fetch_bytes', return_value=pdf_bytes()):
            sid = collector.import_url('https://example.org/paper-for-ui.pdf', 'research')
        pages = [
            {'slug': 'first-reading', 'title': 'First paper reading', 'page_role': 'paper'},
            {'slug': 'older-reading', 'title': 'Older paper reading', 'page_role': 'paper_reading'},
            {'slug': 'shared-concept', 'title': 'Shared concept', 'page_role': 'knowledge'},
        ]
        for page in pages:
            page.update(category='research', page_kind='concept', source_ids=[sid], related_slugs=[], body='# Knowledge\n\nA scoped explanation.')
            (self.ws.wiki / (page['slug'] + '.md')).write_text(page['body'])
            common.save_json(self.ws.meta / (page['slug'] + '.json'), {key: value for key, value in page.items() if key != 'body'})
        with use_workspace(self.ws), patch.object(web, 'wiki_pages', return_value=pages):
            source = web.source(sid).body.decode()
            paper_page = web.page('first-reading').body.decode()
            options = re.search(r'<select data-compile-target>(.*?)</select>', source).group(1)
            self.assertIn('value="shared-concept"', options)
            self.assertNotIn('value="first-reading"', options)
            self.assertNotIn('value="older-reading"', options)
            self.assertNotIn('data-second-pass', source)
            self.assertNotIn('data-second-pass', paper_page)
        (self.ws.root / 'paper-workflow.json').unlink()
        with use_workspace(self.ws), patch.object(web, 'wiki_pages', return_value=pages):
            source = web.source(sid).body.decode()
            paper_page = web.page('first-reading').body.decode()
            options = re.search(r'<select data-compile-target>(.*?)</select>', source).group(1)
            self.assertIn('value="first-reading"', options)
            self.assertIn('value="older-reading"', options)
            self.assertIn('data-second-pass', source)
            self.assertIn('data-second-pass', paper_page)


if __name__ == '__main__':
    unittest.main()
