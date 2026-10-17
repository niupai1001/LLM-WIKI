"""Independent PDF-only reset, backup and per-library admission regressions."""
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from fixture_config import category_fixture
from station import collector, common, jobs, paper_policy, paper_reset
from station.workspaces import Workspace, use_workspace


PDF = '1111111111111111'
TXT = '2222222222222222'
MISSING = '3333333333333333'
NEWS = '4444444444444444'
CORRUPT = '5555555555555555'
NAMED_PDF = '6666666666666666'


def small_pdf():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=144, height=144)
    writer.write(output)
    return output.getvalue()


class PaperResetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture = category_fixture()
        fixture.start()
        self.addCleanup(fixture.stop)

    def seed(self, name):
        ws = Workspace(name, self.root / name)
        with use_workspace(ws):
            db = common.connect()
            try:
                records = [
                    (PDF, 'paper', PDF + '.pdf', small_pdf()),
                    (TXT, 'paper', TXT + '.txt', b'TXT original must be removed.'),
                    (MISSING, 'paper', MISSING + '.pdf', None),
                    (NEWS, 'news', NEWS + '.pdf', small_pdf()),
                    (CORRUPT, 'paper', CORRUPT + '.pdf', b'This file is not a PDF.'),
                ]
                for sid, kind, filename, original in records:
                    record = {'id': sid, 'title': 'Source ' + sid, 'kind': kind,
                              'url': 'https://example.org/source/' + sid, 'category': 'research',
                              'content_status': 'full-text', 'text': 'Archived source text.',
                              'asset_path': filename, 'captured_at': common.utcnow()}
                    if filename.endswith('.pdf'):
                        record['pdf_path'] = filename
                    self.save_record(ws, db, record)
                    if original is not None:
                        (ws.originals / filename).write_bytes(original)
                for table in ('knowledge_coverage', 'paper_distillations', 'knowledge_points',
                              'concept_memberships', 'knowledge_concepts'):
                    db.execute('CREATE TABLE ' + table + '(value TEXT)')
                    db.execute('INSERT INTO ' + table + ' VALUES (?)', ('Old knowledge',))
                db.commit()
            finally:
                db.close()
        (ws.wiki / 'old.md').write_text('# Old knowledge\n\nOld explanation.')
        common.save_json(ws.meta / 'old.json', {'slug': 'old', 'source_ids': [PDF, TXT]})
        common.save_json(ws.pending / 'draft.json', {'slug': 'draft', 'body_markdown': 'Old draft'})
        for folder in ('extractions', 'distillations', 'readings', 'proposal_history', 'claim_reviews', 'runs'):
            common.save_json(ws.root / folder / 'old.json', {'old': True})
        for sid in (PDF, TXT, MISSING, NEWS, CORRUPT):
            common.save_json(ws.root / 'parsed' / (sid + '.json'), {'source_id': sid})
        return ws

    def save_record(self, ws, db, record):
        common.save_json(ws.raw / (record['id'] + '.json'), record)
        db.execute('''INSERT INTO sources
                      (id,title,url,feed,kind,raw_path,captured_at,category,content_status,stage,claim_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                   (record['id'], record['title'], record['url'], 'test', record['kind'],
                    str(ws.raw / (record['id'] + '.json')), common.utcnow(), 'research',
                    'full-text', 'approved', common.utcnow()))

    def source_rows(self, path):
        with sqlite3.connect(path) as db:
            return db.execute('SELECT id,stage,claim_at FROM sources ORDER BY id').fetchall()

    def content_snapshot(self, ws):
        folders = ('raw', 'originals', 'wiki', 'wiki_meta', 'pending', 'parsed',
                   'extractions', 'distillations', 'readings', 'proposal_history', 'claim_reviews', 'runs')
        return {str(path.relative_to(ws.root)): path.read_bytes()
                for folder in folders for path in (ws.root / folder).rglob('*') if path.is_file()}

    def test_reset_backs_up_before_cleaning_and_preserves_only_real_pdf_papers(self):
        ws = self.seed('candidate-v2')
        other = self.seed('other-library')
        old_content = self.content_snapshot(ws)
        other_content = self.content_snapshot(other)
        other_rows = self.source_rows(other.db)
        old_rows = self.source_rows(ws.db)
        backup_root = self.root / 'backups'
        backup = backup_root / ws.library_id
        original_publish = paper_reset.publish

        def inspect_backup_before_publish(actual_ws, changes, proposal_id, **kwargs):
            self.assertEqual(actual_ws, ws)
            self.assertEqual(self.content_snapshot(ws), old_content)
            for relative, content in old_content.items():
                self.assertEqual((backup / relative).read_bytes(), content)
            self.assertEqual(self.source_rows(backup / 'state.db'), old_rows)
            return original_publish(actual_ws, changes, proposal_id, **kwargs)

        with patch.object(paper_reset, 'publish', side_effect=inspect_backup_before_publish) as publish:
            result = paper_reset.reset_library(ws, backup_root)
        publish.assert_called_once()
        self.assertEqual(result['kept_source_ids'], [PDF])
        self.assertEqual({row['source_id'] for row in result['excluded_sources']},
                         {TXT, MISSING, NEWS, CORRUPT})
        self.assertEqual([path.stem for path in ws.raw.glob('*.json')], [PDF])
        self.assertEqual(self.source_rows(ws.db), [(PDF, 'new', None)])
        self.assertEqual([path.name for path in ws.originals.iterdir()], [PDF + '.pdf'])
        self.assertEqual(len(PdfReader(ws.originals / (PDF + '.pdf')).pages), 1)
        self.assertFalse((ws.originals / (TXT + '.txt')).exists())
        self.assertTrue((backup / 'originals' / (TXT + '.txt')).exists())
        self.assertEqual([path.name for path in ws.wiki.iterdir()], ['index.md'])
        self.assertNotIn('Old', (ws.wiki / 'index.md').read_text())
        self.assertEqual(list(ws.meta.glob('*.json')), [])
        self.assertEqual(list(ws.pending.glob('*.json')), [])
        for folder in ('extractions', 'distillations', 'proposal_history', 'claim_reviews', 'runs'):
            self.assertEqual(list((ws.root / folder).glob('*.json')), [], folder)
        self.assertEqual([path.stem for path in (ws.root / 'parsed').glob('*.json')], [PDF])
        policy = common.load_json(ws.root / 'paper-workflow.json')
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['source_kind'], 'paper')
        self.assertEqual(policy['required_original'], 'pdf')
        self.assertFalse(policy['news_extraction'])
        self.assertEqual(common.load_json(backup / 'reset-manifest.json'), result)
        with sqlite3.connect(ws.db) as db:
            for table in ('knowledge_coverage', 'paper_distillations', 'knowledge_points',
                          'concept_memberships', 'knowledge_concepts'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
            self.assertEqual(db.execute('SELECT index_dirty FROM publication_state WHERE library_id=?',
                                        (ws.library_id,)).fetchone()[0], 1)
        self.assertEqual(self.content_snapshot(other), other_content)
        self.assertEqual(self.source_rows(other.db), other_rows)
        self.assertFalse((other.root / 'paper-workflow.json').exists())

    def test_kept_pdf_is_preserved_by_its_recorded_path_even_without_id_filename(self):
        ws = self.seed('named-assets')
        with sqlite3.connect(ws.db) as db:
            self.save_record(ws, db, {'id': NAMED_PDF, 'title': 'Publisher PDF', 'kind': 'paper',
                                     'url': 'https://example.org/publisher-copy',
                                     'pdf_path': 'publisher-copy.pdf', 'content_status': 'full-text',
                                     'text': 'Original publisher source.'})
        (ws.originals / 'publisher-copy.pdf').write_bytes(small_pdf())
        result = paper_reset.reset_library(ws, self.root / 'named-backups')
        self.assertIn(NAMED_PDF, result['kept_source_ids'])
        self.assertIsNotNone(paper_policy.pdf_original(ws, common.load_json(ws.raw / (NAMED_PDF + '.json'))))
        self.assertEqual(len(PdfReader(ws.originals / 'publisher-copy.pdf').pages), 1)

    def test_queued_or_running_job_refuses_reset_without_removing_content(self):
        for status in ('queued', 'running'):
            with self.subTest(status=status):
                ws = self.seed('blocked-' + status)
                job = jobs.enqueue(ws, 'compile', {'source_ids': [PDF]})
                if status == 'running':
                    self.assertEqual(jobs.claim(ws)['id'], job['id'])
                content = self.content_snapshot(ws)
                rows = self.source_rows(ws.db)
                backup_root = self.root / ('backup-' + status)
                with self.assertRaisesRegex(ValueError, 'Finish or cancel'):
                    paper_reset.reset_library(ws, backup_root)
                self.assertEqual(self.content_snapshot(ws), content)
                self.assertEqual(self.source_rows(ws.db), rows)
                self.assertFalse((ws.root / 'paper-workflow.json').exists())
                self.assertFalse((backup_root / ws.library_id).exists())
                self.assertEqual(jobs.get(ws, job['id'])['status'], status)

    def test_policy_is_library_local_and_collector_rejects_text_and_news(self):
        ws = self.seed('pdf-policy')
        other = self.seed('compatible-legacy')
        paper_reset.reset_library(ws, self.root / 'policy-backups')
        input_text = self.root / 'user-source.txt'
        input_text.write_text('A caller supplied text original.')
        input_pdf = self.root / 'user-source.pdf'
        input_pdf.write_bytes(small_pdf())
        self.assertTrue(paper_policy.enabled(ws))
        self.assertFalse(paper_policy.enabled(other))
        with use_workspace(ws):
            with self.assertRaisesRegex(ValueError, 'PDF论文'):
                collector.import_file('Text paper', input_text, 'research', kind='paper')
            with self.assertRaisesRegex(ValueError, 'PDF论文'):
                collector.import_file('News PDF', input_pdf, 'research', kind='news')
            db = common.connect()
            try:
                before = self.source_rows(ws.db)
                with patch.object(collector, '_original') as fetch:
                    self.assertIsNone(collector.archive({'kind': 'news', 'url': 'https://example.org/news',
                                                         'title': 'News', 'category': 'research'}, db))
                    fetch.assert_not_called()
                self.assertIsNone(collector.archive({'kind': 'paper', 'url': 'https://example.org/text-paper',
                                                     'title': 'Text only', 'category': 'research',
                                                     'text': 'This has caller-provided text but no PDF.'}, db))
                self.assertEqual(self.source_rows(ws.db), before)
                self.assertFalse(any(path.suffix in {'.md', '.txt'} for path in ws.originals.iterdir()))
            finally:
                db.close()
            sid = collector.import_file('New PDF', input_pdf, 'research', kind='paper')
            record = common.load_json(ws.raw / (sid + '.json'))
            self.assertIsNotNone(paper_policy.pdf_original(ws, record))
            self.assertEqual(record['kind'], 'paper')
        with use_workspace(other):
            sid = collector.import_file('Legacy text remains compatible', input_text, 'research', kind='paper')
            self.assertEqual(common.load_json(other.raw / (sid + '.json'))['asset_path'], sid + '.txt')
        self.assertFalse((other.root / 'paper-workflow.json').exists())


if __name__ == '__main__':
    unittest.main()
