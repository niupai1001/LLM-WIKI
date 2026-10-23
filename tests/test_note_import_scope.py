"""Explicit note admission does not weaken the default PDF library policy."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from station import collector
from station.workspaces import Workspace,use_workspace

class NoteImportScopeTests(unittest.TestCase):
    def test_only_configured_category_accepts_note_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ws=Workspace('preview',root)
            (root/'paper-workflow.json').write_text(json.dumps({'enabled':True,'note_import_categories':['disaster']}))
            note=root/'input.md';note.write_text('# 洪水分割\n\n传感器与标注需匹配。')
            database=Mock();database.execute.return_value.fetchone.return_value=None
            with use_workspace(ws),patch.object(collector,'validate_category',side_effect=lambda c,**kw:c),patch.object(collector,'connect',return_value=database):
                sid=collector.import_file('洪水分割',note,category='disaster',kind='note')
                record=json.loads((ws.raw/(sid+'.json')).read_text())
                self.assertEqual(record['kind'],'note')
                self.assertEqual(record['text'],note.read_text())
                self.assertEqual(len(sid),16)
                with self.assertRaisesRegex(ValueError,'只接收PDF'):
                    collector.import_file('洪水分割',note,category='agents',kind='note')
                self.assertEqual(len(list(ws.raw.glob('*.json'))),1)
                with self.assertRaisesRegex(ValueError,'只接收PDF'):
                    collector.import_file('洪水分割',note,category='disaster',kind='paper')
