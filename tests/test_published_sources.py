import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from station import published_sources as papers

URL = 'https://proceedings.iclr.cc/paper_files/paper/2024/hash/abcdef-Abstract-Conference.html'
PDF = 'https://proceedings.iclr.cc/paper_files/paper/2024/file/abcdef-Paper-Conference.pdf'


def declared_page(pdf_url=PDF, date='2024-05-31', doi=''):
    return f'''<html><head>
      <meta name="citation_title" content="An Agent Benchmark">
      <meta name="citation_author" content="Liu, Xiao">
      <meta name="citation_journal_title" content="International Conference on Learning Representations">
      <meta name="citation_volume" content="2024">
      <meta name="citation_publication_date" content="{date}">
      <meta name="citation_pdf_url" content="{pdf_url}">
      <meta name="citation_doi" content="{doi}">
    </head></html>'''.encode()


class PublishedSourcesTests(unittest.TestCase):
    def test_only_official_paper_pages_qualify(self):
        self.assertTrue(papers.is_publication_page(URL))
        self.assertTrue(papers.is_publication_page('https://aclanthology.org/2024.acl-long.123/'))
        self.assertTrue(papers.is_publication_page('http://jmlr.org/papers/v27/26-0434.html'))
        self.assertFalse(papers.is_publication_page('https://aclanthology.org/posts/announce_news/'))
        self.assertFalse(papers.is_publication_page('https://example.org/paper.html'))
        self.assertFalse(papers.is_publication_page(PDF))

    def test_official_metadata_keeps_unknown_doi_unknown(self):
        result = papers.resolve_publication(URL, lambda _: declared_page())
        bibliography = result['bibliography']
        self.assertEqual(bibliography['authors'][0]['name'], 'Xiao Liu')
        self.assertEqual(bibliography['publication_record']['date'], '2024-05-31')
        self.assertEqual(bibliography['original_version'], 'published')
        self.assertEqual(bibliography['publication_doi'], '')
        self.assertEqual(bibliography['publication_doi_status'], 'not-provided-on-official-site')

    def test_untrusted_or_non_pdf_links_are_rejected(self):
        for pdf_url in ['http://127.0.0.1/secret.pdf', 'https://example.org/counterfeit.pdf', 'https://proceedings.iclr.cc/abstract.html']:
            with self.subTest(pdf_url=pdf_url), self.assertRaises(ValueError):
                papers.resolve_publication(URL, lambda _: declared_page(pdf_url))

    def test_an_abstract_does_not_count_as_full_paper(self):
        def fetch(url):
            return declared_page() if url == URL else b'%PDF-1.7\nnot real content'
        with patch('station.collector._pdf_text', return_value='Only the abstract'), patch('station.collector._asset') as asset:
            with self.assertRaisesRegex(ValueError, '全文'):
                papers.capture_published_original(URL, 'abc123', fetch)
            asset.assert_not_called()

    def test_upgrading_version_preserves_identity_chronology_and_preprint(self):
        original = {'id': 'same-id', 'url': 'https://arxiv.org/abs/1234.12345', 'pdf_path': 'same-id.pdf',
                    'bibliography': {'arxiv_id': '1234.12345', 'arxiv_version': 2, 'first_published': '2023-01-01',
                                     'publication_status': 'arxiv-preprint'}}
        official = {'pdf_path': 'same-id-published.pdf', 'bibliography': papers.resolve_publication(URL, lambda _: declared_page())['bibliography']}
        result = papers.merge_published_version(original, official)
        self.assertEqual(result['id'], original['id'])
        self.assertEqual(result['bibliography']['first_published'], '2023-01-01')
        self.assertEqual(result['bibliography']['publication_date'], '2024-05-31')
        self.assertEqual(result['archived_versions'][0]['pdf_path'], 'same-id.pdf')
        self.assertTrue(result['archived_versions'][0]['same_work'])
        self.assertEqual(papers.merge_published_version(result, official)['archived_versions'], result['archived_versions'])
        self.assertEqual(original['pdf_path'], 'same-id.pdf')

    def test_year_only_date_is_not_fabricated_as_a_day(self):
        result = papers.resolve_publication(URL, lambda _: declared_page(date='2024'))
        self.assertEqual(result['bibliography']['publication_record']['date_precision'], 'year')
        self.assertEqual(result['bibliography']['publication_date'], '2024')

    def test_full_text_capture_keeps_official_metadata_and_pdf(self):
        pdf = b'%PDF-1.7\nvalidated fixture'
        def fetch(url):
            return declared_page(doi='10.1234/verified') if url == URL else pdf
        from station import collector
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(papers, 'ROOT', root), patch.object(collector, 'ASSETS', root / 'originals'), patch.object(collector, '_pdf_text', return_value='Full paper mechanism. ' * 50):
                original = papers.capture_published_original(URL, 'abc123', fetch)
                self.assertEqual(original['content_status'], 'full-text')
                self.assertEqual(original['bibliography']['publication_doi'], '10.1234/verified')
                self.assertEqual((root / 'originals' / original['pdf_path']).read_bytes(), pdf)
                self.assertTrue((root / original['bibliography']['metadata_snapshot']).is_file())


if __name__ == '__main__':
    unittest.main()
