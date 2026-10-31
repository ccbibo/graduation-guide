"""Synthetic conversation contract checks; no embedding/model queries."""
import copy
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.request import build_opener,HTTPCookieProcessor,Request
from urllib.error import HTTPError
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import conversation as c
import server
from backend import PublicError

CID='S04#b85cee0260a4152a#ck1#c001'
class FakeBackend:
    def __init__(self):self.calls=[]
    def scope(self):return {'engine':{'state':'synthetic_test','generative':False}}
    def source(self,cid,full=False):return {'chunk_id':cid,'source_id':'S04','text':'fixture-only'}
    def query(self,body,search_text=None):
        self.calls.append((copy.deepcopy(body),search_text))
        if body['query_text']=='論文失敗測試':raise PublicError('fixture_failure','合成故障',503)
        if body['query_text']=='未知新主題測試':
            return {'ok':True,'question_id':'q-'+str(len(self.calls)),'query_text':body['query_text'],'decision':'C','sources':[],'clarify_questions':[],'conditions':body['conditions'],'clarify_skipped':body['clarify_skipped'],'reason_codes':['no_match'],'conflicts':[]}
        qs=[{'slot':'dept','question':'請填系所','code':'needs_dept'}] if body['query_text']=='論文須補問' else []
        return {'ok':True,'question_id':'q-'+str(len(self.calls)),'query_text':body['query_text'],'decision':'B','sources':[{'chunk_id':CID,'source_id':'S04'}],'clarify_questions':qs,'conditions':body['conditions'],'clarify_skipped':body['clarify_skipped'],'reason_codes':[],'conflicts':[]}

class ConversationHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.SESSIONS.clear();cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.http.backend=FakeBackend();cls.base=f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close()
    def setUp(self):self.opener=build_opener(HTTPCookieProcessor(CookieJar()));self.req('/')
    def req(self,path,body=None,opener=None):
        headers={'Origin':self.base,'Content-Type':'application/json'}
        q=Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        try:
            with (opener or self.opener).open(q,timeout=3) as r:return r.status,json.loads(r.read()) if path.startswith('/api') else r.read()
        except HTTPError as e:
            status=e.code;data=json.loads(e.read());e.close();return status,data
    def chat(self):return self.req('/api/conversations',{})[1]['conversation']['conversation_id']
    def query(self,cid,text,**kw):return self.req('/api/query',{'conversation_id':cid,'query_text':text,**kw})
    def ack(self,cid,result):return self.req('/api/conversation/commit',{'conversation_id':cid,'question_id':result['question_id'],'commit_token':result['commit_token']})
    def get(self,cid):return self.req('/api/conversation?conversation_id='+cid)
    def test_commit_is_required_idempotent_and_bound(self):
        a,b=self.chat(),self.chat();_,r=self.query(a,'論文幾本？')
        self.assertEqual(self.get(a)[1]['conversation']['turns'],[])
        self.assertEqual(self.ack(b,r)[0],409)
        _,committed=self.ack(a,r);self.assertEqual(len(committed['conversation']['turns']),1)
        _,again=self.ack(a,r);self.assertEqual(len(again['conversation']['turns']),1)
        self.assertEqual(self.get(b)[1]['conversation']['turns'],[])
    def test_short_followup_and_personal_slots(self):
        a=self.chat();_,r=self.query(a,'論文紙本要交幾本？',conditions={'degree_level':'碩士','dept':'工科系'});self.ack(a,r)
        _,r=self.query(a,'那註冊組呢');self.assertEqual(r['query_text'],'那註冊組呢');self.assertIn('註冊組',self.http.backend.calls[-1][1]);self.assertIn('成績登錄',self.http.backend.calls[-1][1]);self.assertEqual(r['conditions']['dept'],'工科系');self.ack(a,r)
        _,r=self.query(a,'那博士呢');self.assertEqual(r['conditions']['degree_level'],'博士');self.ack(a,r)
        _,r=self.query(a,'論文可以單面列印嗎？');self.assertEqual(self.http.backend.calls[-1][1],'論文可以單面列印嗎？');self.ack(a,r)
        _,r=self.query(a,'那80頁呢');self.assertIn('80頁',self.http.backend.calls[-1][1]);self.assertIn('單面',self.http.backend.calls[-1][1])
    def test_routing_does_not_call_engine_or_pollute_topic(self):
        a=self.chat();_,r=self.query(a,'論文紙本要交幾本？');self.ack(a,r);n=len(self.http.backend.calls)
        for text,kind in [('你好','greeting'),('謝謝','acknowledgement'),('你能幫我什麼','capabilities'),('今天天氣如何？','clear_out_of_scope')]:
            _,r=self.query(a,text);self.assertEqual(r['response_kind'],kind);self.assertEqual(r['sources'],[]);self.ack(a,r)
        self.assertEqual(len(self.http.backend.calls),n)
        _,r=self.query(a,'那註冊組呢');self.assertEqual(r['response_kind'],'normal');self.assertIn('成績登錄',self.http.backend.calls[-1][1])
    def test_no_prior_ambiguous_and_negated_degree(self):
        a=self.chat();n=len(self.http.backend.calls)
        for text in ['那註冊組呢','要幾本？']:
            _,r=self.query(a,text);self.assertEqual(r['response_kind'],'needs_context');self.ack(a,r)
        self.assertEqual(len(self.http.backend.calls),n)
        self.assertEqual(self.get(a)[1]['conversation']['remembered_conditions'],{})
    def test_sources_conversation_and_session_isolation(self):
        a,b=self.chat(),self.chat();_,r=self.query(a,'論文幾本？')
        path=f'/api/source/S04?conversation_id={a}&chunk_id={quote(CID,safe='')}'
        self.assertEqual(self.req(path)[0],403);self.ack(a,r);self.assertEqual(self.req(path)[0],200)
        self.assertEqual(self.req(path.replace(a,b))[0],403)
        other=build_opener(HTTPCookieProcessor(CookieJar()));self.req('/',opener=other)
        self.assertEqual(self.req('/api/conversation?conversation_id='+a,opener=other)[0],404)
        self.req('/api/conversation/reset',{'conversation_id':a});self.assertEqual(self.req(path)[0],403)
    def test_reset_invalidates_pending_and_preserves_other_chat(self):
        a,b=self.chat(),self.chat();_,r=self.query(a,'論文幾本？',conditions={'degree_level':'碩士'})
        self.req('/api/conversation/reset',{'conversation_id':a});self.assertEqual(self.ack(a,r)[0],409)
        self.assertEqual(self.get(a)[1]['conversation']['remembered_conditions'],{})
        self.assertEqual(self.get(b)[0],200)
    def test_failure_unacknowledged_and_old_clarify(self):
        a=self.chat();_,r=self.query(a,'論文須補問');self.ack(a,r);old=r
        _,r=self.query(a,'謝謝');self.ack(a,r)
        status,_=self.query(a,old['query_text'],conditions={'dept':'工科系'},parent_question_id=old['question_id']);self.assertEqual(status,409)
        before=len(self.get(a)[1]['conversation']['turns']);self.assertEqual(self.query(a,'論文失敗測試')[0],503)
        _,r=self.query(a,'論文幾本？') # simulate cancelled response: never commit
        self.assertEqual(len(self.get(a)[1]['conversation']['turns']),before)
    def test_client_context_forgery_and_limits(self):
        a=self.chat();self.assertEqual(self.query(a,'論文幾本？',runtime={'message_id':'fake'})[0],400)
        for _ in range(server.MAX_CHATS-1):self.chat()
        self.assertEqual(self.req('/api/conversations',{})[0],409)
    def test_profile_result_and_committed_condition_agree(self):
        a=self.chat();before=len(self.http.backend.calls)
        _,r=self.query(a,'我是博士');self.assertEqual(r['response_kind'],'profile_update')
        self.assertEqual(r['conditions'],{'degree_level':'博士'})
        _,saved=self.ack(a,r);self.assertEqual(saved['conversation']['remembered_conditions'],r['conditions'])
        self.assertEqual(len(self.http.backend.calls),before)

    def test_short_followup_keeps_recipient_and_new_unknown_subject_clears_old(self):
        a=self.chat();_,r=self.query(a,'論文紙本要交幾本？');self.ack(a,r)
        _,r=self.query(a,'那註冊組呢');self.ack(a,r)
        _,r=self.query(a,'要多久？');self.assertIn('註冊組',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'校內文件去哪裡下載？');self.ack(a,r)
        before=len(self.http.backend.calls);_,r=self.query(a,'要幾本？')
        self.assertEqual(r['response_kind'],'needs_context');self.assertEqual(len(self.http.backend.calls),before)

    def test_explicit_degree_corrections_and_revocation_persist(self):
        a=self.chat();_,r=self.query(a,'我是碩士');self.ack(a,r)
        _,r=self.query(a,'不是碩士，是博士');self.assertEqual(r['conditions']['degree_level'],'博士');self.assertEqual(r['query_text'],'不是碩士，是博士');self.ack(a,r)
        _,r=self.query(a,'不是博士，是碩士');self.assertEqual(r['conditions']['degree_level'],'碩士');self.ack(a,r)
        _,r=self.query(a,'更正，我是博士，紙本論文要交幾本？');self.assertEqual(r['conditions']['degree_level'],'博士');self.assertEqual(self.http.backend.calls[-1][0]['query_text'],'更正，我是博士，紙本論文要交幾本？');self.ack(a,r)
        _,r=self.query(a,'我不是博士');self.assertNotIn('degree_level',r['conditions']);self.ack(a,r)
        saved=self.get(a)[1]['conversation'];self.assertNotIn('degree_level',saved['remembered_conditions']);self.assertIn('degree_level',saved['pending_conditions'])
        _,r=self.query(a,'論文紙本要交幾本？');self.assertNotIn('degree_level',self.http.backend.calls[-1][0]['conditions'])

    def test_degree_followup_keeps_recipient_focus(self):
        a=self.chat();_,r=self.query(a,'碩士論文紙本要交幾本給圖書館？');self.ack(a,r)
        _,r=self.query(a,'那教務處呢？');self.ack(a,r)
        _,r=self.query(a,'那博士呢？');body,search=self.http.backend.calls[-1]
        self.assertEqual(body['conditions']['degree_level'],'博士');self.assertIn('教務處',search);self.assertNotIn('碩士',search);self.ack(a,r)
        _,r=self.query(a,'那碩士呢？');self.assertIn('教務處',self.http.backend.calls[-1][1]);self.assertEqual(r['conditions']['degree_level'],'碩士')

    def test_natural_followup_intents_and_topic_priority(self):
        a=self.chat();_,r=self.query(a,'口試申請要怎麼辦？');self.ack(a,r)
        _,r=self.query(a,'由誰送給註冊組？');self.assertEqual(r['context']['intent'],'sender');self.assertIn('學位考試申請',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'那要多久？');self.assertEqual(r['context']['intent'],'duration');self.ack(a,r)
        _,r=self.query(a,'論文延後公開要什麼時候申請？');self.ack(a,r)
        _,r=self.query(a,'那繳紙本時要帶什麼？');self.assertEqual(r['context']['intent'],'paper_documents');self.assertIn('延後公開',self.http.backend.calls[-1][1]);self.assertNotIn('二本',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'所以一定用最新版本就好嗎？');self.assertEqual(r['context']['intent'],'version_applicability');self.assertIn('延後公開',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'借的書要怎麼處理？');self.assertEqual(r['context']['intent'],'library_returns');self.assertIn('借書歸還',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'該去哪個單位或網頁確認？');self.assertEqual(r['context']['intent'],'confirmation');self.assertIn('借書歸還',self.http.backend.calls[-1][1])

    def test_greeting_and_acknowledgement_are_not_mechanical(self):
        a=self.chat();_,r=self.query(a,'你好');self.assertEqual(r['title'],'你好，歡迎');self.ack(a,r)
        _,r=self.query(a,'謝謝');self.assertEqual(r['suggestions'],[]);self.assertIn('不客氣',r['message'])

    def test_weather_closing_thanks_and_real_thanks_followup(self):
        a=self.chat();_,r=self.query(a,'論文可以單面列印嗎？');self.ack(a,r);topic_id=r['question_id'];before=len(self.http.backend.calls)
        _,r=self.query(a,'請問今天台北會下雨嗎？');self.assertEqual(r['response_kind'],'clear_out_of_scope');self.ack(a,r)
        _,r=self.query(a,'謝啦，我先去準備論文了');self.assertEqual(r['response_kind'],'acknowledgement');self.assertEqual(r['suggestions'],[]);self.ack(a,r)
        self.assertEqual(len(self.http.backend.calls),before)
        _,r=self.query(a,'謝謝，那80頁呢');self.assertEqual(r['response_kind'],'normal');self.assertEqual(r['context']['previous_question_id'],topic_id);self.assertIn('80頁',self.http.backend.calls[-1][1]);self.assertEqual(r['query_text'],'謝謝，那80頁呢')

    def test_new_followups_keep_the_actual_phase(self):
        a=self.chat();_,r=self.query(a,'口試申請怎麼辦？');self.ack(a,r)
        _,r=self.query(a,'那學生自己送也可以嗎？');self.assertEqual(r['context']['intent'],'sender_permission');self.assertIn('學位考試申請',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'註冊組會在幾個工作天內審好？');self.assertEqual(r['context']['intent'],'processing_duration');self.assertIn('工作天數',self.http.backend.calls[-1][1]);self.assertNotIn('份數',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'論文延後公開要什麼時候申請？');self.ack(a,r)
        _,r=self.query(a,'佐證影本可以嗎？');self.assertEqual(r['context']['intent'],'paper_documents');self.assertIn('延後公開',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'申請書和佐證資料要一起裝訂嗎？');self.assertEqual(r['context']['intent'],'paper_documents');self.assertIn('延後公開',self.http.backend.calls[-1][1]);self.ack(a,r)
        _,r=self.query(a,'2024年入學的博士也適用這份格式條例嗎？');self.assertEqual(r['context']['intent'],'version_applicability');self.assertIn('格式規定',self.http.backend.calls[-1][1]);self.assertNotIn('enrollment_year',r['conditions'])

    def test_office_alias_query_has_no_answer_number(self):
        a=self.chat();_,r=self.query(a,'論文紙本要交幾本給圖書館？');self.ack(a,r)
        _,r=self.query(a,'那教務處呢？');search=self.http.backend.calls[-1][1]
        self.assertIn('教務處 註冊組 學位考試成績登錄 修訂完成論文',search);self.assertNotIn('一本',search);self.assertNotIn('二本',search);self.ack(a,r)
        _,r=self.query(a,'那博士呢？');search=self.http.backend.calls[-1][1];self.assertIn('教務處',search);self.assertIn('成績登錄',search)

    def test_unrecognized_empty_retrieval_is_friendly_not_asserted_outside(self):
        a=self.chat();_,r=self.query(a,'未知新主題測試');self.assertEqual(r['response_kind'],'needs_topic');self.assertEqual(r['sources'],[]);self.assertIn('主要協助',r['message']);self.assertNotIn('無法列出辦理步驟',r['message'])

    def test_cookie_is_port_isolated(self):
        jar=CookieJar();op=build_opener(HTTPCookieProcessor(jar));self.req('/',opener=op)
        self.assertEqual([x.name for x in jar],[f'campus_chat_sid_{self.http.server_port}'])

class MemoryUnit(unittest.TestCase):
    def test_pending_expiry_and_version(self):
        m=c.new_memory();r={'question_id':'q-x','query_text':'你好','sources':[]};p={'response_kind':'greeting'}
        token=c.stage(m,r,p,now=10);self.assertEqual(c.commit(m,token,'q-x',now=101),(False,'commit_expired'))
        token=c.stage(m,r,p,now=200);m['version']+=1;self.assertEqual(c.commit(m,token,'q-x',now=201),(False,'conversation_changed'))
    def test_year_not_inferred_and_skip_removes_memory(self):
        m=c.new_memory();m['conditions']={'enrollment_year':114,'degree_level':'碩士'}
        p=c.plan({'query_text':'論文格式規定？','conditions':{},'clarify_skipped':['degree_level']},m)
        self.assertNotIn('degree_level',p['conditions']);self.assertEqual(p['conditions']['enrollment_year'],114)
        p=c.plan({'query_text':'更正成115年','conditions':{},'clarify_skipped':[]},m);self.assertEqual(p['response_kind'],'needs_context')
    def test_limit_does_not_delete_committed_history(self):
        m=c.new_memory();m['turns']=[{'question_id':str(i),'query_text':'你好','sources':[]} for i in range(c.MAX_TURNS)]
        token=c.stage(m,{'question_id':'overflow','query_text':'你好','sources':[]},{'response_kind':'greeting'})
        self.assertEqual(c.commit(m,token,'overflow'),(False,'conversation_limit'));self.assertEqual(len(m['turns']),c.MAX_TURNS)

if __name__=='__main__':unittest.main()
