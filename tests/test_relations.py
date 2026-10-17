import copy
import json
import unittest
from unittest.mock import patch

from station import wiki,common,collector,knowledge,web
from station.relations import validate_relations
import test_autonomous_wiki as workflow
import test_rewrite as fixtures


class RelationValidationTests(unittest.TestCase):
    def setUp(self):
        self.a,self.b='a'*16,'b'*16
        self.claims=[{'id':'ca','source_ids':[self.a],'evidence_passages':[{'source_id':self.a}]},
                     {'id':'cb','source_ids':[self.b],'evidence_passages':[{'source_id':self.b}]}]
        self.relation={'id':'shared','kind':'shared_mechanism','target_slug':'other',
                       'summary':'两份来源都把真实执行反馈用于下一次决策，但修改对象不同。',
                       'scope':'只比较原文描述的机制，不推断通用性能优势。','claim_ids':['ca','cb'],'source_ids':[self.a,self.b]}

    def validate(self,value):
        return validate_relations([value],self.claims,{self.a,self.b},{'current','other'},'current')

    def test_relation_keeps_claims_and_source_provenance(self):
        result=self.validate(self.relation)[0]
        self.assertEqual(result['claim_ids'],['ca','cb'])
        self.assertEqual(result['source_ids'],[self.a,self.b])

    def test_editorial_basis_is_retained_and_unknown_basis_is_rejected(self):
        self.assertEqual(self.validate({**self.relation, 'basis': 'editorial'})[0]['basis'], 'editorial')
        with self.assertRaises(ValueError):
            self.validate({**self.relation, 'basis': 'unverified-kind'})

    def test_unknown_target_claim_or_unquoted_source_is_blocked(self):
        for field,value in [('target_slug','missing'),('claim_ids',['invented']),('source_ids',['c'*16])]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.validate({**self.relation,field:value})
        self.claims[1]['evidence_passages']=[]
        with self.assertRaises(ValueError):self.validate(self.relation)

    def test_cross_study_relation_cannot_use_one_source(self):
        with self.assertRaises(ValueError):self.validate({**self.relation,'claim_ids':['ca'],'source_ids':[self.a]})


class RelationCompilationTests(unittest.TestCase):
    setUp=workflow.AutonomousWikiTests.setUp
    tearDown=fixtures.RewriteTests.tearDown
    new_source=workflow.AutonomousWikiTests.new_source
    model_reply=workflow.AutonomousWikiTests.model_reply
    status=fixtures.RewriteTests.status

    def test_approved_original_can_support_another_concept_without_status_reset(self):
        with patch.object(wiki,'call_llm',return_value=self.model_reply(self.sid)):
            result=wiki.compile_and_publish(source_ids=[self.sid],revisit=True)
        self.assertEqual(result['published'],1)
        self.assertEqual(self.status(),'approved')
        self.assertTrue(self.page.exists())
        self.assertTrue((wiki.WIKI/'new-method.md').exists())

    def test_explicit_cross_category_update_preserves_both_classifications(self):
        sid=self.new_source();collector.set_category(sid,'vision')
        reply=self.model_reply(sid);reply.update(action='update',slug='concept',title=self.meta['title'])
        reply['body_markdown']=self.rewritten+'\n\n'+reply['body_markdown']
        reply['claims'].insert(0,self.meta['claims'][0])
        with patch.object(wiki,'call_llm',return_value=reply):
            result=wiki.compile_and_publish(source_ids=[sid],target_slug='concept')
        self.assertEqual(result['published'],1)
        self.assertEqual(common.load_json(self.meta_path)['category'],'agents')
        with common.connect() as db:
            self.assertEqual(db.execute('SELECT category FROM sources WHERE id=?',(sid,)).fetchone()[0],'vision')

    def test_relations_have_explanations_in_graph_and_collection_is_secondary(self):
        sid=self.new_source()
        def model(messages):
            context=json.loads(messages[-1]['content'].split('JSON数据：\n',1)[1])
            self.assertIn('concept',[p['slug'] for p in context['candidates']])
            reply=self.model_reply(sid)
            reply['body_markdown']+=f'两种实现都受容量限制，机制关系见[[concept]]。[S:{self.sid}]'
            reply['claims'][0]['source_ids'].append(self.sid)
            reply['claims'][0]['evidence_passages'].append({'source_id':self.sid,'excerpt_id':'e1'})
            reply['relations']=[{'id':'selection-common','target_slug':'concept','kind':'shared_mechanism',
                'summary':'两个实现都在受测容量范围内选择有用指令，实验条件仍分别保留。',
                'scope':'只对应提供的两个来源，不能推广其他任务。','claim_ids':['selection'],'source_ids':[sid,self.sid]}]
            return reply
        with patch.object(wiki,'call_llm',side_effect=model):result=wiki.compile_and_publish(source_ids=[sid])
        self.assertEqual(result['published'],1)
        edges=knowledge.graph_data()['edges']
        edge=next(e for e in edges if e['type']=='shared_mechanism')
        self.assertIn('容量',edge['description']);self.assertEqual(edge['label'],'共同机制')
        self.assertEqual(set(edge['source_ids']),{self.sid,sid})
        path = wiki.META / 'new-method.json'
        meta = common.load_json(path)
        meta['relations'][0]['basis'] = 'editorial'
        common.save_json(path, meta)
        editorial = next(e for e in knowledge.graph_data()['edges'] if e['type'] == 'shared_mechanism')
        self.assertEqual(editorial['basis'], 'editorial')
        self.assertIn('编辑关联', editorial['label'])
        (wiki.WIKI/'batch.md').write_text('# 批次原件清单\n\n[[new-method]]')
        common.save_json(wiki.META/'batch.json',{'category':'agents','page_kind':'topic','page_role':'source_collection','source_ids':[sid]})
        self.assertNotIn('wiki:batch',{n['id'] for n in knowledge.graph_data()['nodes']})
        pages=knowledge.wiki_pages();pages.append({'slug':'batch','title':'批次原件清单','body':'资料列表','category':'agents','page_kind':'topic','page_role':'source_collection','source_ids':[]})
        with patch.object(web,'wiki_pages',return_value=pages),patch.object(web,'PENDING',wiki.PENDING):
            html=web.home().body.decode()
        self.assertGreater(html.index('批次原件清单'),html.index('概念知识'))
        self.assertIn('<summary>资料清单',html)
        collector.set_category(sid,'vision')
        with patch.object(web,'wiki_pages',return_value=pages),patch.object(web,'PENDING',wiki.PENDING):
            filtered=web.home(category='vision').body.decode()
        self.assertIn('href="/page/new-method"',filtered)
