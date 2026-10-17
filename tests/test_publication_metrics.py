import copy
import unittest

from station import citations, web
from station.publication_metrics import (
    normalize_publication, publication_fields, publication_summary_html,
    validate_publication_metrics,
)


SID = '1111111111111111'


def paper(venue_type='journal'):
    return {'id': SID, 'title': 'A test paper', 'kind': 'paper',
            'bibliography': {'original_version': 'published', 'publication_status': 'published',
                             'doi': '10.1234/example', 'arxiv_id': '2301.12345',
                             'publication_record': {
                                 'status': 'verified-on-official-site', 'venue': 'Example Research',
                                 'venue_type': venue_type, 'year': 2023,
                                 'rankings': [{'system': 'Example ranking', 'rank': 'A', 'year': 2022,
                                               'status': 'verified', 'source_url': 'https://rankings.example.org/2022',
                                               'checked_at': '2026-10-15T00:00:00Z'}],
                                 'impact_factor': {'value': 8.4, 'year': 2024, 'status': 'verified',
                                                   'source_url': 'https://journals.example.org/metrics/2024',
                                                   'checked_at': '2026-10-15T00:00:00Z'}}}}


class PublicationMetricsTests(unittest.TestCase):
    def test_preserves_metric_year_separately_and_does_not_mutate_versions_or_doi(self):
        record = paper()
        original = copy.deepcopy(record)
        result = normalize_publication(record)
        self.assertEqual(result['year'], 2023)
        self.assertEqual(result['rankings'][0]['year'], 2022)
        self.assertEqual(result['impact_factor']['year'], 2024)
        self.assertEqual(record, original)
        self.assertEqual(validate_publication_metrics(record), [])
        rendered = dict(publication_fields(record))
        self.assertIn('JIF，2024', ' '.join(rendered))
        self.assertIn('2026-10-15T00:00:00Z', rendered['期刊影响因子（JIF，2024）'])
        self.assertIn('https://journals.example.org/metrics/2024', rendered['期刊影响因子（JIF，2024）'])

    def test_conference_has_rank_but_never_a_journal_impact_factor(self):
        record = paper('conference')
        result = normalize_publication(record)
        self.assertEqual(len(result['rankings']), 1)
        self.assertIsNone(result['impact_factor'])
        self.assertIn('conference', record['bibliography']['publication_record']['venue_type'])
        self.assertEqual(len(validate_publication_metrics(record)), 1)
        rendered = publication_summary_html(result)
        self.assertIn('Example ranking A（2022）', rendered)
        self.assertNotIn('JIF', rendered)

    def test_missing_optional_metrics_are_valid_and_old_records_still_show_venue(self):
        record = paper()
        published = record['bibliography']['publication_record']
        for key in ('rankings', 'impact_factor', 'venue_type', 'year'):
            published.pop(key)
        published['date'] = '2023'
        self.assertEqual(validate_publication_metrics(record), [])
        self.assertEqual(publication_fields(record), [('发表载体', 'Example Research · 2023')])
        self.assertEqual(publication_fields({}), [])

    def test_crossref_publication_confirmation_does_not_relabel_archived_preprint(self):
        record = paper()
        bibliography = record['bibliography']
        bibliography['original_version'] = 'preprint'
        bibliography['publication_status'] = 'arxiv-preprint'
        bibliography['publication_record']['status'] = 'verified-via-crossref'
        bibliography['publication_record']['metadata_source_url'] = 'https://api.crossref.org/works/10.1234/example'
        original = copy.deepcopy(record)
        self.assertTrue(normalize_publication(record)['published'])
        self.assertIn(('期刊', 'Example Research · 2023'), publication_fields(record))
        self.assertEqual(validate_publication_metrics(record), [])
        self.assertIn('预印本', web.source_version_tag(record))
        self.assertNotIn('正式发表版本', web.source_version_tag(record))
        self.assertEqual(record, original)

    def test_no_unverified_or_provenance_free_metrics_are_shown(self):
        for field in ('status', 'year', 'source_url', 'checked_at'):
            record = paper()
            publication = record['bibliography']['publication_record']
            publication['impact_factor'].pop(field)
            publication['rankings'][0].pop(field)
            result = normalize_publication(record)
            self.assertIsNone(result['impact_factor'], field)
            self.assertEqual(result['rankings'], [], field)
            self.assertEqual(len(validate_publication_metrics(record)), 2, field)
        record = paper()
        record['bibliography']['publication_record']['status'] = 'model-suggested'
        self.assertEqual(publication_fields(record), [])

    def test_bad_numbers_dates_and_urls_cannot_be_presented_as_metrics(self):
        invalid = [('value', True), ('value', -1), ('value', 'nan'), ('value', 'inf'),
                   ('year', True), ('year', '2024 or 2025'), ('checked_at', 'recently'),
                   ('source_url', 'javascript:alert(1)'), ('source_url', 'https://secret@metrics.example.org'),
                   ('source_url', 'https://[')]
        for key, value in invalid:
            record = paper()
            record['bibliography']['publication_record']['impact_factor'][key] = value
            self.assertIsNone(normalize_publication(record)['impact_factor'], (key, value))
        record = paper()
        record['bibliography']['publication_record']['impact_factor']['value'] = 0
        self.assertEqual(normalize_publication(record)['impact_factor']['value'], 0)

    def test_normalization_tolerates_malformed_legacy_inputs(self):
        for record in (None, [], {'bibliography': []}, {'bibliography': {'publication_record': []}}):
            self.assertEqual(publication_fields(record), [])
            self.assertEqual(validate_publication_metrics(record), [])
        record = paper()
        record['bibliography']['publication_record']['rankings'] = 'CCF A'
        self.assertEqual(normalize_publication(record)['rankings'], [])
        self.assertTrue(validate_publication_metrics(record))

    def test_source_and_citation_show_confirmed_metadata_with_escaped_values(self):
        record = paper()
        publication = record['bibliography']['publication_record']
        publication['venue'] = '<script>Venue</script>'
        publication['rankings'][0]['rank'] = '<img src=x>'
        rendered = web.bibliography_html(record)
        self.assertIn('期刊影响因子（JIF，2024）', rendered)
        self.assertIn('Example ranking', rendered)
        self.assertIn('&lt;script&gt;Venue', rendered)
        self.assertNotIn('<script>Venue', rendered)
        _, entries = citations.prepare_citations(f'[S:{SID}]', lookup=lambda _: record)
        rendered = citations.evidence_html(entries)
        self.assertIn('JIF 8.4（2024）', rendered)
        self.assertIn('Example ranking', rendered)
        self.assertIn('&lt;img src=x&gt;', rendered)
        self.assertNotIn('<img src=x>', rendered)


if __name__ == '__main__':
    unittest.main()
