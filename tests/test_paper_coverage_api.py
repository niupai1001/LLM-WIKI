import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from station.paper_rebuild import initialize
from station.v2web import api_coverage
from station.workspaces import Workspace, use_workspace


class PaperCoverageApiTests(unittest.TestCase):
    def test_library_specific_points_expose_concept_links_without_old_question_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            libraries = []
            for name in ('legacy', 'candidate-v2'):
                ws = Workspace(name, Path(folder) / name)
                initialize(ws)
                (ws.root / 'paper-workflow.json').write_text(json.dumps({'enabled': True}))
                with sqlite3.connect(ws.db) as db:
                    db.execute('INSERT INTO paper_distillations VALUES (?,?,?,?,?,?)',
                               ('1111111111111111', 'version-' + name, 'paper-one', '{}', '[]', 'now'))
                    db.execute('INSERT INTO knowledge_points VALUES (?,?,?,?,?)',
                               ('kp_11111111111111111111', '1111111111111111', 'k1', 'version-' + name,
                                json.dumps({'proposition': name + ' knowledge', 'scope': 'Conditions'})))
                    db.execute('INSERT INTO concept_memberships VALUES (?,?,?)',
                               (name + '-concept', 'kp_11111111111111111111', 'Explains this concept'))
                libraries.append(ws)
            for ws in libraries:
                with use_workspace(ws):
                    result = api_coverage()
                self.assertEqual(result['library_id'], ws.library_id)
                self.assertEqual(result['coverage_mode'], 'paper_knowledge_points')
                self.assertEqual(result['questions'], [])
                self.assertEqual(result['knowledge_points'][0]['concept_slugs'], [ws.library_id + '-concept'])
                self.assertEqual(result['knowledge_points'][0]['proposition'], ws.library_id + ' knowledge')
                self.assertEqual(result['papers'][0]['source_version'], 'version-' + ws.library_id)
