import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import bibliography, collector, common, citations, web

SID = '1111111111111111'
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
<id>http://arxiv.org/abs/2610.12345v2</id><title>A Real Paper</title><published>2026-10-07T15:00:00Z</published><updated>2026-10-08T16:00:00Z</updated>
<author><name>Alice Smith</name><arxiv:affiliation>Example University</arxiv:affiliation></author><author><name>Bob Lee</name></author>
<arxiv:journal_ref>Journal reference supplied by submitter</arxiv:journal_ref></entry></feed>'''


class BibliographyTests(unittest.TestCase):
    def test_published_original_displays_publication_date_without_losing_preprint(self):
        record={'id':SID,'title':'Tool paper','kind':'paper','asset_path':'formal.pdf','bibliography':{
            'arxiv_id':'2301.12345','arxiv_version':3,'original_version':'published','publication_status':'published',
            'first_published':'2023-01-10','publication_date':'2024-05-31','preprint_pdf_path':'old.pdf',
            'authors':[{'name':'Formal Author'}],'publication_record':{'status':'verified-on-official-site','venue':'ICLR','date':'2024-05-31','year':2024}}}
        rendered=web.bibliography_html(record)
        self.assertIn('ICLR · 正式发表版本',rendered)
        self.assertIn('正式发表 2024-05-31',rendered)
        self.assertIn('首次公开日期（预印本）',rendered)
        self.assertNotIn('归档为 arXiv 版本',rendered)
        _,entries=citations.prepare_citations(f'[S:{SID}]',[],lambda _:record)
        self.assertEqual(entries[0].published,'2024-05-31')

    def test_atom_dates_versions_missing_doi_and_preprint_status_are_explicit(self):
        metadata = bibliography.parse_arxiv_atom(ATOM, 'https://export.arxiv.org/api/query')["2610.12345"]
        self.assertEqual(metadata['arxiv_version'], 2)
        self.assertEqual(metadata['first_published'], '2026-10-07T15:00:00Z')
        self.assertEqual(metadata['updated'], '2026-10-08T16:00:00Z')
        self.assertEqual(metadata['authors'][0]['affiliations'], ['Example University'])
        self.assertEqual(metadata['doi'], '')
        self.assertEqual(metadata['doi_status'], 'not-provided')
        self.assertEqual(metadata['publication_status'], 'arxiv-preprint')
        self.assertEqual(metadata['metadata_status'], 'verified')

    def test_no_fabricated_arxiv_doi_or_first_publication_from_rss(self):
        record = {'url': 'https://arxiv.org/abs/2610.12345', 'kind': 'paper', 'published': 'Fri, 09 Oct 2026 00:00:00 -0400', 'captured_at': '2026-10-10T00:00:00Z', 'content_status': 'full-text'}
        metadata = bibliography.build_bibliography(record)
        self.assertEqual(metadata['first_published'], '')
        self.assertEqual(metadata['doi_status'], 'unverified')
        self.assertEqual(metadata['arxiv_version'], None)
        self.assertEqual(metadata['arxiv_doi'], '')
        self.assertEqual(metadata['announced_at'], record['published'])

    def test_preprint_doi_only_from_actual_official_page_link(self):
        page = '<a id="arxiv-doi-link" href="https://doi.org/10.48550/arXiv.2610.12345">DOI</a>'
        result = bibliography.arxiv_doi_metadata(page, 'https://arxiv.org/abs/2610.12345')
        self.assertEqual(result['arxiv_doi'], '10.48550/arXiv.2610.12345')
        self.assertEqual(result['arxiv_doi_status'], 'verified-on-arxiv')
        self.assertEqual(bibliography.arxiv_doi_metadata('<p>No DOI</p>', 'https://arxiv.org/abs/2610.12345')['arxiv_doi'], '')

    def test_affiliations_from_explicit_html_preserve_api_author_provenance(self):
        page = '<div class="ltx_role_author"><span class="ltx_personname">Bob Lee</span><span class="ltx_role_affiliation">Affiliation: Example Lab</span></div><p>Not an affiliation: Imaginary University</p>'
        official = bibliography.parse_arxiv_atom(ATOM, 'https://export.arxiv.org/api/query')['2610.12345']
        record = {'url': 'https://arxiv.org/abs/2610.12345', 'kind': 'paper', 'text_original_url': 'https://arxiv.org/html/2610.12345v2'}
        metadata = bibliography.build_bibliography(record, official, page)
        self.assertEqual(metadata['authors'][1]['affiliations'], ['Example Lab'])
        self.assertEqual(metadata['field_sources']['authors']['method'], 'arxiv-api')
        self.assertEqual(metadata['field_sources']['affiliations']['method'], 'archived-html-author-affiliations')
        self.assertNotIn('Imaginary University', metadata['affiliations'])

    def test_metadata_html_and_selection_are_escaped_and_collapsible(self):
        record = {'id': SID, 'title': 'Original', 'kind': 'paper', 'content_status': 'full-text', 'url': 'https://arxiv.org/abs/2610.12345', 'asset_path': 'paper.pdf',
                  'bibliography': {'authors': [{'name': '<script>evil</script>', 'affiliations': []}], 'arxiv_id': '2610.12345', 'arxiv_version': 2, 'first_published': '2026-10-07', 'doi_status': 'not-provided', 'publication_status': 'arxiv-preprint'},
                  'selection': {'status': '候选资料', 'reason': '<img onerror=evil>', 'limitations': '尚未核对实验', 'checked_at': '2026-10-10'}}
        rendered = web.bibliography_html(record)
        self.assertIn('<details class="source-bibliography">', rendered)
        self.assertNotIn('<details class="source-bibliography" open', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<img', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('官方API未提供出版 DOI', rendered)
        self.assertIn('尚未核对实验', rendered)

    def test_citation_prefers_verified_first_date_and_displays_both_doi_types(self):
        record = {'title': 'Original paper', 'published': '2026-10-09', 'url': 'https://arxiv.org/abs/2610.12345',
                  'bibliography': {'first_published': '2026-10-07T15:00:00Z', 'authors': [{'name': 'Alice', 'affiliations': []}], 'doi': '10.1234/paper', 'arxiv_doi': '10.48550/arXiv.2610.12345', 'publication_status': 'arxiv-preprint'}}
        rendered, entries = citations.prepare_citations('[S:' + SID + ']', lookup=lambda sid: record)
        self.assertIn('发布日期 2026-10-07', rendered)
        reference = citations.evidence_html(entries)
        self.assertIn('出版 DOI', reference)
        self.assertIn('预印本 DOI', reference)
        self.assertIn('Alice', reference)

    def test_batch_refresh_preserves_originals_and_uses_cache_with_failures_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patches = [patch.object(common, name, value) for name, value in {'ROOT': root, 'RAW': root/'raw', 'ASSETS': root/'originals', 'WIKI': root/'wiki', 'META': root/'wiki_meta', 'PENDING': root/'pending', 'DB': root/'state.db'}.items()]
            patches += [patch.object(collector, 'RAW', root/'raw'), patch.object(collector, 'ASSETS', root/'originals')]
            for p in patches: p.start()
            try:
                db = common.connect()
                sid = collector.archive({'url':'https://arxiv.org/abs/2610.12345v2', 'title':'Original', 'kind':'paper', 'category':'ai', 'text':'Original full text'}, db)
                db.execute("UPDATE sources SET stage='approved' WHERE id=?", (sid,));db.commit()
                original = common.load_json(common.RAW/f'{sid}.json')
                with patch.object(collector, 'fetch_bytes', return_value=ATOM) as fetch:
                    report = collector.refresh_bibliographies([sid])
                    self.assertEqual(report['verified'], 1)
                    collector.refresh_bibliographies([sid])
                    fetch.assert_called_once()
                updated = common.load_json(common.RAW/f'{sid}.json')
                for field in original:
                    if field != 'bibliography': self.assertEqual(updated[field], original[field])
                self.assertEqual(updated['bibliography']['arxiv_version'], 2)
                self.assertEqual(db.execute('SELECT stage FROM sources WHERE id=?',(sid,)).fetchone()[0], 'approved')
                for p in (root/'source_metadata').glob('*'): p.unlink()
                with patch.object(collector, 'fetch_bytes', side_effect=OSError('https://secret@example.org')):
                    collector.refresh_bibliographies([sid])
                failed = common.load_json(common.RAW/f'{sid}.json')['bibliography']
                self.assertEqual(failed['metadata_status'], 'verified')
                self.assertEqual(failed['last_query_status'], 'failed')
                self.assertNotIn('secret', failed['query_error'])
                db.close()
            finally:
                for p in reversed(patches): p.stop()

    def test_auto_import_title_and_preprint_doi_use_official_records(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            paths = {'ROOT': root, 'RAW': root/'raw', 'ASSETS': root/'originals', 'WIKI': root/'wiki', 'META': root/'wiki_meta', 'PENDING': root/'pending', 'DB': root/'state.db'}
            for module in [common, collector]:
                for name, value in paths.items():
                    if hasattr(module, name): stack.enter_context(patch.object(module, name, value))
            with common.connect() as db:
                sid = collector.archive({'url':'https://arxiv.org/abs/2610.12345v2', 'kind':'paper', 'category':'ai', 'text':'Original full text'}, db)
            page = b'<a id="arxiv-doi-link" href="https://doi.org/10.48550/arXiv.2610.12345">DOI</a>'
            report = collector.refresh_bibliographies([sid], fetcher=lambda url, **kwargs: ATOM if '/api/query' in url else page, verify_doi=True)
            updated = common.load_json(common.RAW/f'{sid}.json')
            self.assertEqual(report['verified'], 1)
            self.assertEqual(updated['title'], 'A Real Paper')
            self.assertEqual(updated['bibliography']['arxiv_doi'], '10.48550/arXiv.2610.12345')
            self.assertEqual(updated['text'], 'Original full text')


if __name__ == '__main__': unittest.main()
