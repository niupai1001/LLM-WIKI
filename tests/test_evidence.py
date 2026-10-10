import os
import subprocess
import sys
import tempfile
import unittest


class EvidenceTests(unittest.TestCase):
    def isolated(self, script):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True,
                                    env=dict(os.environ, STATION_DATA=root))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_metadata_never_claims_verified_or_conflict_free(self):
        self.isolated('''
from station.evidence import knowledge_evidence, source_evidence
from station.common import ROOT
page = knowledge_evidence('missing')
assert '没有来源登记' in page and '没有内容核查记录' in page
assert '未登记差异或冲突；这不代表已排除矛盾' in page
assert 'Codex 核查' in page and '不是同行评审' in page
assert '来源元数据未核实' in source_evidence({})
assert '没有来源登记' in knowledge_evidence('../outside')
assert list(ROOT.iterdir()) == [], 'Read-only helpers created files or data directories'
''')

    def test_source_metadata_verification_is_not_peer_review_and_is_escaped(self):
        self.isolated('''
from station.evidence import source_evidence
record = {'kind':'paper','content_status':'missing','asset_path':'scan.pdf',
 'bibliography':{'metadata_status':'verified','publication_status':'journal-reference-provided',
 'method':'<script>fake()</script>','checked_at':'2026-10-10','metadata_source_url':'javascript:alert(1)',
 'field_sources':{'title':'official','authors':'official'}}}
page = source_evidence(record)
assert '来源元数据已核对' in page and '同行评审状态未核实' in page
assert '已登记归档原件' in page and '缺少可提取的全文文本' in page
assert '&lt;script&gt;' in page and '<script>' not in page and 'javascript:' not in page
assert 'title' in page and 'authors' in page
''')

    def test_arxiv_versions_and_doi_urls_are_not_independent_works(self):
        self.isolated('''
from station.common import META, RAW, save_json
from station.evidence import knowledge_evidence
ids = ['0000000000000001','0000000000000002','0000000000000003']
sources = [
 {'url':'https://arxiv.org/abs/2601.12345v1','bibliography':{'doi':'10.1000/EXAMPLE'}},
 {'url':'https://arxiv.org/pdf/2601.12345v2'},
 {'url':'https://doi.org/10.1000/example'}]
for sid, record in zip(ids, sources):
 save_json(RAW/(sid+'.json'),dict(record,id=sid,title='研究',kind='paper',content_status='full-text'))
save_json(META/'topic.json',{'source_ids':ids,'claims':[{'text':'一个研究结果','source_ids':ids,'scope':'原实验设置','status':'多源一致'}]})
page=knowledge_evidence('topic')
assert '登记来源 3 条' in page and '按作品标识去重 1 组' in page
assert '去重后不足两份作品' in page and '来源独立性尚未完整核实' in page
''')

    def test_claim_scope_and_both_conflict_sides_are_visible_without_a_winner(self):
        self.isolated('''
from station.common import META, RAW, save_json
from station.evidence import knowledge_evidence
a,b='0000000000000001','0000000000000002'
for sid, url in [(a,'https://arxiv.org/abs/2601.12345'),(b,'https://arxiv.org/abs/2601.54321')]:
 save_json(RAW/(sid+'.json'),{'id':sid,'title':'<研究 '+sid+'>','url':url,'kind':'paper','content_status':'full-text'})
save_json(META/'topic.json',{'source_ids':[a,b], 'content_review':{'by':'Codex','at':'2026-10-10','notes':'对照原文核查'},
 'independent_source_groups':[{'id':'研究A','source_ids':[a],'basis':'作者与数据集已区分','status':'verified'},
 {'id':'研究B','source_ids':[b],'basis':'独立复现记录已核对','status':'verified'}],
 'claims':[{'text':'限定范围内的一致观察','source_ids':[a,b],'scope':'小规模模拟环境','status':'多源一致'}],
 'conflicts':[{'title':'设置不同','status':'范围已区分','handling':'并列保留，不作横向排名',
 'sides':[{'text':'A在设置一较好','source_ids':[a],'scope':'设置一'}, {'text':'B在设置二较好','source_ids':[b],'scope':'设置二'}]}]})
page=knowledge_evidence('topic')
assert '2 组，覆盖 2/2' in page and '多源一致' in page
assert '适用范围：小规模模拟环境' in page and '适用范围：设置一' in page and '适用范围：设置二' in page
assert 'A在设置一较好' in page and 'B在设置二较好' in page
assert '并列保留，不作横向排名' in page and '不按数量或模型意见自动选胜者' in page
assert '&lt;研究' in page and '<研究' not in page
assert page.count('href="/source/') >= 6
''')

    def test_missing_scope_source_fulltext_and_malformed_records_downgrade(self):
        self.isolated('''
from station.common import META, RAW, save_json
from station.evidence import knowledge_evidence
a,b='0000000000000001','0000000000000002'
save_json(RAW/(a+'.json'),{'id':a,'title':'摘要','url':'https://example.org/a','content_status':'summary-only'})
save_json(META/'topic.json',{'source_ids':[a,b], 'claims':[None,{'text':'<img src=x onerror=alert(1)>','source_ids':[a,b],'status':'单源支持'}],
 'conflicts':[{'title':'未完整核查','status':'范围已区分','sides':[{'text':'只记录一侧','source_ids':[a]}]}]})
page=knowledge_evidence('topic')
assert '适用范围未记录' in page and '来源证据不完整' in page and '部分来源缺少全文文本' in page
assert '本地记录缺失，未核实' in page and '处理状态未核实' in page
assert '&lt;img' in page and '<img' not in page
save_json(META/'broken.json',{'source_ids':{},'claims':'broken','conflicts':[None],'content_review':[]})
assert '尚未建立逐项断言证据记录' in knowledge_evidence('broken')
''')


if __name__ == '__main__':
    unittest.main()
