"""Library writing pauses are enforced before work or model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from station import jobs, paper_policy, wiki, worker
from station.workspaces import Workspace, use_workspace


class ManualWritingPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ws = Workspace('test', Path(temporary.name))

    def configure(self, **config):
        (self.ws.root / 'paper-workflow.json').write_text(json.dumps(config))

    def test_opt_in_and_library_isolation(self):
        paper_policy.require_automatic_writing(self.ws)
        self.configure(enabled=True)
        paper_policy.require_automatic_writing(self.ws)
        self.configure(writing_executor='codex_manual')
        with self.assertRaisesRegex(paper_policy.AutomaticWritingPaused, '未接入 Codex API'):
            paper_policy.require_automatic_writing(self.ws)
        with tempfile.TemporaryDirectory() as other:
            paper_policy.require_automatic_writing(Workspace('other', Path(other)))

    def test_prepare_and_rewrite_refuse_before_database_or_model(self):
        self.configure(writing_executor='codex_manual')
        with use_workspace(self.ws), patch.object(wiki, 'call_llm') as model, patch.object(wiki, 'connect') as database:
            for entry in (lambda: wiki.prepare(), lambda: wiki.prepare_rewrite('page')):
                with self.assertRaises(paper_policy.AutomaticWritingPaused):
                    entry()
            model.assert_not_called()
            database.assert_not_called()

    def test_already_queued_writing_fails_without_invoking_handler(self):
        for kind in ('compile', 'synthesize'):
            with self.subTest(kind=kind):
                job = jobs.enqueue(self.ws, kind, {})
                self.configure(writing_executor='codex_manual')
                handler = Mock()
                result = worker.run_once(self.ws, {kind: handler})
                self.assertEqual(result['id'], job['id'])
                self.assertEqual(result['status'], 'failed')
                self.assertIn('codex_manual', result['error'])
                handler.assert_not_called()

    def test_legacy_prepare_also_reads_its_library_policy(self):
        self.configure(writing_executor='codex_manual')
        with patch.object(wiki, 'ROOT', self.ws.root), patch.object(wiki, 'call_llm') as model:
            with self.assertRaises(paper_policy.AutomaticWritingPaused):
                wiki.prepare()
            with self.assertRaises(paper_policy.AutomaticWritingPaused):
                wiki.prepare_rewrite('page')
            model.assert_not_called()

    def test_query_still_runs_in_paused_library(self):
        self.configure(writing_executor='codex_manual')
        jobs.enqueue(self.ws, 'query', {'question': 'test'})
        handler = Mock(return_value={'answer': 'ok'})
        with patch('station.publication.recover'):
            result = worker.run_once(self.ws, {'query': handler})
        self.assertEqual(result['status'], 'succeeded')
        handler.assert_called_once()


if __name__ == '__main__':
    unittest.main()
