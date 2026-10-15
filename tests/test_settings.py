"""Configuration writes never mutate originals and protect stale browser forms."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = []
        for name, filename in {'TOPICS_FILE': 'config/categories.json', 'FEEDS_FILE': 'config/feeds.json',
                               'LOCK_FILE': 'config/.settings.lock', 'DB': 'data/state.db',
                               'RAW': 'data/raw', 'META': 'data/wiki_meta', 'PENDING': 'data/pending'}.items():
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if name in ('RAW', 'META', 'PENDING'):
                path.mkdir()
            patcher = patch.object(settings, name, path)
            patcher.start()
            self.patches.append(patcher)
        self.topics = [{'id': 'agents', 'name': 'Agent 智能体'}, {'id': 'other', 'name': '其他'}]
        self.feeds = [{'name': 'Agent 论文', 'url': 'https://example.org/agents.xml', 'kind': 'paper', 'category': 'agents', 'enabled': True, 'custom_field': 'keep me'}]
        self.write()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def write(self):
        settings.TOPICS_FILE.write_text(json.dumps(self.topics))
        settings.FEEDS_FILE.write_text(json.dumps(self.feeds))

    def payload(self, **kwargs):
        return dict(version=settings.snapshot()['version'], **kwargs)

    def test_snapshot_keeps_legacy_files_and_id_stable(self):
        original = settings.FEEDS_FILE.read_bytes()
        a, b = settings.snapshot(), settings.snapshot()
        self.assertEqual(a, b)
        self.assertEqual(settings.FEEDS_FILE.read_bytes(), original)
        self.assertTrue(a['topics'][0]['enabled'])
        self.assertEqual(a['topics'][0]['description'], '')
        self.assertTrue(a['feeds'][0]['id'].startswith('feed-'))

    def test_edit_persists_stable_id_and_unedited_fields(self):
        feed = settings.snapshot()['feeds'][0]
        result = settings.save_feed(self.payload(id=feed['id'], name='更新名称', include_keywords=['agent', 'AGENT', 'memory']))
        saved = settings.snapshot()['feeds'][0]
        self.assertEqual(saved['id'], feed['id'])
        self.assertEqual(saved['include_keywords'], ['agent', 'memory'])
        self.assertEqual(saved['custom_field'], 'keep me')
        self.assertEqual(saved['name'], '更新名称')
        self.assertIn('已保存', result['message'])

    def test_stale_version_rejected_without_write(self):
        old = self.payload(name='新主题')
        settings.save_topic(self.payload(name='先保存的主题'))
        original = settings.TOPICS_FILE.read_bytes()
        with self.assertRaises(settings.SettingsConflict):
            settings.save_topic(old)
        self.assertEqual(settings.TOPICS_FILE.read_bytes(), original)

    def test_new_topic_and_deletion(self):
        new = settings.save_topic(self.payload(name='Agent 安全', description='安全边界'))['topics'][-1]
        self.assertTrue(new['id'].startswith('topic-'))
        settings.delete_topic(new['id'], self.payload())
        self.assertEqual(len(settings.snapshot()['topics']), 2)

    def test_unknown_edit_ids_do_not_create(self):
        with self.assertRaisesRegex(ValueError, '不存在'):
            settings.save_topic(self.payload(id='missing', name='新主题'))
        with self.assertRaisesRegex(ValueError, '不存在'):
            settings.save_feed(self.payload(id='missing', name='新订阅', url='https://example.org/a'))

    def test_unused_other_can_be_paused_and_removed(self):
        settings.save_topic(self.payload(id='other', enabled=False))
        self.assertFalse(next(t for t in settings.snapshot()['topics'] if t['id']=='other')['enabled'])
        settings.delete_topic('other', self.payload())
        self.assertNotIn('other',[t['id'] for t in settings.snapshot()['topics']])

    def test_pause_topic_disables_feeds_resume_does_not(self):
        saved = settings.save_topic(self.payload(id='agents', enabled=False))
        self.assertFalse(saved['topics'][0]['enabled'])
        self.assertFalse(saved['feeds'][0]['enabled'])
        self.assertIn('同时停用了 1', saved['message'])
        settings.save_topic(self.payload(id='agents', enabled=True))
        self.assertFalse(settings.snapshot()['feeds'][0]['enabled'])

    def test_topic_with_subscription_protected_including_disabled(self):
        settings.save_topic(self.payload(id='agents', enabled=False))
        with self.assertRaisesRegex(ValueError, '订阅节点'):
            settings.delete_topic('agents', self.payload())

    def test_topic_with_sources_protected(self):
        self.feeds = []
        self.write()
        with sqlite3.connect(settings.DB) as db:
            db.execute('CREATE TABLE sources (id TEXT, category TEXT)')
            db.execute("INSERT INTO sources VALUES ('source-one','agents')")
        with self.assertRaisesRegex(ValueError, '原始资料'):
            settings.delete_topic('agents', self.payload())

    def test_wiki_and_pending_references_protected(self):
        self.feeds = []
        self.write()
        for directory, label in ((settings.META, 'Wiki'), (settings.PENDING, '草稿')):
            record = directory / 'example.json'
            record.write_text(json.dumps({'category': 'agents'}))
            with self.assertRaisesRegex(ValueError, label):
                settings.delete_topic('agents', self.payload())
            record.unlink()

    def test_duplicate_canonical_feed_rejected(self):
        with self.assertRaisesRegex(ValueError, '相同地址'):
            settings.save_feed(self.payload(name='复制', url='https://EXAMPLE.org/agents.xml/?utm_source=test#fragment', category='agents'))

    def test_legacy_duplicate_can_edit_but_not_add_duplicate(self):
        self.feeds.append(dict(self.feeds[0], name='旧示例', enabled=False))
        self.write()
        feed = settings.snapshot()['feeds'][0]
        settings.save_feed(self.payload(id=feed['id'], include_keywords=['agent']))
        with self.assertRaisesRegex(ValueError, '相同地址'):
            settings.save_feed(self.payload(name='新重复', url=feed['url']))

    def test_invalid_url_rejected_without_fetch(self):
        for url in ('file:///tmp/a', 'http://localhost/feed', 'http://127.0.0.1/rss', 'http://127.1/rss',
                    'https://user:pass@example.org/feed', 'https://example.org:8443/feed', 'https://exa mple.org/feed',
                    'https://bad_host.example.org/feed'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                settings.save_feed(self.payload(name='无效地址', url=url))

    def test_invalid_fields_rejected(self):
        invalid = [{'enabled': 'false'}, {'kind': []}, {'max_age_days': True}, {'max_age_days': 0},
                   {'include_keywords': 'agent'}, {'require_keywords': [123]}, {'exclude_keywords': ['a'] * 51},
                   {'category': 'missing'}, {'name': ''}]
        feed = settings.snapshot()['feeds'][0]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                settings.save_feed(self.payload(id=feed['id'], **fields))

    def test_enabled_subscription_rejects_disabled_topic(self):
        settings.save_topic(self.payload(id='agents', enabled=False))
        feed = settings.snapshot()['feeds'][0]
        with self.assertRaisesRegex(ValueError, '主题已停用'):
            settings.save_feed(self.payload(id=feed['id'], enabled=True))

    def test_delete_feed_preserves_archived_files(self):
        record = settings.RAW / 'original.json'
        record.write_text('original')
        feed = settings.snapshot()['feeds'][0]
        settings.delete_feed(feed['id'], self.payload())
        self.assertEqual(settings.snapshot()['feeds'], [])
        self.assertEqual(record.read_text(), 'original')

    def test_second_file_failure_rolls_back_topics(self):
        topics_before, feeds_before = settings.TOPICS_FILE.read_bytes(), settings.FEEDS_FILE.read_bytes()
        original = settings._atomic_bytes
        failed = False
        def write(path, content):
            nonlocal failed
            if path == settings.FEEDS_FILE and not failed:
                failed = True
                raise OSError('simulated write failure')
            return original(path, content)
        with patch.object(settings, '_atomic_bytes', side_effect=write), self.assertRaisesRegex(ValueError, '恢复原配置'):
            settings.save_topic(self.payload(id='agents', enabled=False))
        self.assertEqual(settings.TOPICS_FILE.read_bytes(), topics_before)
        self.assertEqual(settings.FEEDS_FILE.read_bytes(), feeds_before)
