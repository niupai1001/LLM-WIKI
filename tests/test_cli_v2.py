"""Exercise the real command dispatcher, not just argparse help."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class CliV2Tests(unittest.TestCase):
    def test_library_index_search_recovery_and_evaluation_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copytree(Path(__file__).resolve().parent.parent/'config',root/'config')
            env=dict(os.environ,STATION_DATA=str(root),EMBEDDING_BASE_URL='',EMBEDDING_MODEL='',EMBEDDING_API_KEY='')
            def run(*arguments):
                result=subprocess.run([sys.executable,'-m','station.cli',*arguments],env=env,capture_output=True,text=True,check=True)
                return json.loads(result.stdout)
            self.assertEqual(run('library','list')['active'],'legacy')
            self.assertEqual(run('library','init','candidate-v2')['library_id'],'candidate-v2')
            prefix=('--library-id','candidate-v2')
            self.assertEqual(run(*prefix,'index')['library_id'],'candidate-v2')
            self.assertEqual(run(*prefix,'search','测试概念')['results'],[])
            self.assertEqual(run(*prefix,'jobs'),[])
            self.assertIsInstance(run(*prefix,'recover'),list)
            self.assertFalse(run(*prefix,'evaluate')['passed'])
            self.assertEqual(run('library','list')['active'],'legacy')
