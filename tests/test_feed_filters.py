import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from station.collector import entry_matches, ingest


class FeedFilterTests(unittest.TestCase):
    def test_relevance_and_required_model_context(self):
        rules = {'include_keywords': ['agent', 'agentic', '智能体'], 'require_keywords': ['llm', 'language models']}
        self.assertTrue(entry_matches({'title': 'Agentic planning', 'summary': 'LLM tool use'}, rules))
        self.assertTrue(entry_matches({'title': '智能体研究', 'summary': 'Language models'}, rules))
        self.assertFalse(entry_matches({'title': 'Reagent analysis', 'summary': 'LLM'}, rules))
        self.assertFalse(entry_matches({'title': 'Classic agent simulation'}, rules))

    def test_recent_window_excludes_missing_stale_future_dates(self):
        rules = {'max_age_days': 7}
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        for value in ['2026-10-01T00:00:00Z', '', '2026-10-11T00:00:00Z']:
            self.assertFalse(entry_matches({'published': value}, rules, now))
        self.assertTrue(entry_matches({'published': 'Thu, 08 Oct 2026 00:00:00 -0400'}, rules, now))
        self.assertTrue(entry_matches({'updated': '2026-10-09T00:00:00Z'}, rules, now))

    def test_exclusion_matches_decoded_plain_feed_text(self):
        rules = {'include_keywords': ['agent'], 'exclude_keywords': ['sponsored']}
        self.assertFalse(entry_matches({'title': 'Agent', 'summary': '<p>SPONSORED</p>'}, rules))
        self.assertTrue(entry_matches({'title': 'Agent&#32;research'}, rules))

    def test_filter_before_limit_and_duplicates_do_not_use_quota(self):
        import feedparser
        entries = [feedparser.FeedParserDict(title='Unrelated', link='https://example.org/unrelated') for _ in range(15)]
        entries += [feedparser.FeedParserDict(title='Agent already archived', link='https://example.org/existing'), feedparser.FeedParserDict(title='Agent paper', link='https://example.org/one'), feedparser.FeedParserDict(title='Agent tools', link='https://example.org/two')]
        parsed = feedparser.FeedParserDict(entries=entries, bozo=False)
        feed = {'name': 'Test', 'url': 'https://example.org/feed', 'include_keywords': ['agent']}
        config = {'topics': [{'id': 'other', 'enabled': True}], 'feeds': [feed]}
        with patch('station.settings.snapshot', return_value=config), patch('station.collector.connect') as connect, patch('station.collector.fetch_bytes', return_value=b'feed'), patch('feedparser.parse', return_value=parsed), patch('station.collector.archive', return_value='new') as archive, patch.dict('os.environ', {'MAX_PER_FEED': '1'}):
            connect.return_value.execute.return_value.fetchone.side_effect = [('existing',), None, None]
            self.assertEqual(ingest(), 1)
            self.assertEqual(archive.call_count, 1)
            self.assertEqual(archive.call_args.args[0]['url'], 'https://example.org/one')

    def test_ingest_uses_current_settings_and_skips_disabled_topics(self):
        import feedparser
        config = {'topics': [{'id': 'agents', 'enabled': True}, {'id': 'ai', 'enabled': False}],
                  'feeds': [{'name':'Current', 'url':'https://example.org/current', 'category':'agents', 'enabled':True},
                            {'name':'Disabled topic', 'url':'https://example.org/disabled', 'category':'ai', 'enabled':True},
                            {'name':'Disabled node', 'url':'https://example.org/stopped', 'category':'agents', 'enabled':False}]}
        empty = feedparser.FeedParserDict(entries=[], bozo=False)
        with patch('station.settings.snapshot', return_value=config) as snapshot, patch('station.collector.connect'), \
             patch('station.collector.fetch_bytes', return_value=b'feed') as fetch, patch('feedparser.parse', return_value=empty):
            self.assertEqual(ingest(), 0)
            snapshot.assert_called_once()
            fetch.assert_called_once_with('https://example.org/current')


if __name__ == '__main__':
    unittest.main()
