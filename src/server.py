"""Loopback-only desktop chat; bounded session-owned in-memory conversations."""
from __future__ import annotations
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit
from backend import VectorBackend, PublicError, validate_query
import conversation as convo

WEB = Path(__file__).parent / "web"
MAX_BODY = 16_384
SESSION_TTL = 3600
CHAT_TTL = 3600
MAX_CHATS = 16
MAX_SESSIONS = 64
SESSIONS = {}
LOCK = threading.RLock()
QUERY_GATE = threading.BoundedSemaphore(1)

class Handler(BaseHTTPRequestHandler):
    server_version = "CampusRAGDesktop/0.3"
    def log_message(self, fmt, *args):
        pass
    def headers_safe(self, status, kind, size, cookie=None):
        cookie = cookie or getattr(self, "_active_sid", None)
        self.send_response(status)
        for k,v in [("Content-Type",kind),("Content-Length",str(size)),("Cache-Control","no-store"),("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]: self.send_header(k,v)
        if cookie: self.send_header("Set-Cookie",f"campus_chat_sid_{self.server.server_port}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}")
        self.end_headers()
    def reply(self,obj,status=200,cookie=None):
        data=json.dumps(obj,ensure_ascii=False).encode()
        try:
            self.headers_safe(status,"application/json; charset=utf-8",len(data),cookie);self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError): pass
    def fail(self,code,message,status=400): return self.reply({"ok":False,"error":{"code":code,"message":message}},status)
    def same_host(self): return self.headers.get("Host")==f"127.0.0.1:{self.server.server_port}"
    def session(self,create=False):
        try:
            cookie=SimpleCookie(self.headers.get("Cookie",""));key=f"campus_chat_sid_{self.server.server_port}";sid=cookie.get(key).value if cookie.get(key) else None
        except Exception: sid=None
        now=time.monotonic()
        with LOCK:
            for k in list(SESSIONS):
                if now-SESSIONS[k]["at"]>SESSION_TTL: del SESSIONS[k]
            if sid in SESSIONS:
                SESSIONS[sid]["at"]=now;self._active_sid=sid;return sid,SESSIONS[sid]
            if create and len(SESSIONS)<MAX_SESSIONS:
                sid=secrets.token_urlsafe(24);SESSIONS[sid]={"at":now,"chats":{}};self._active_sid=sid;return sid,SESSIONS[sid]
        return None,None
    def chat(self,session,cid,touch=True):
        if not isinstance(cid,str) or cid not in session["chats"]: raise PublicError("conversation_not_found","找不到這個聊天，或它不屬於目前工作階段。",404)
        chat=session["chats"][cid]
        if time.monotonic()-chat["at"]>CHAT_TTL: raise PublicError("conversation_expired","這個聊天已超過一小時未使用，請開始新聊天。",410)
        if touch: chat["at"]=time.monotonic()
        return chat
    def snapshot(self,cid,chat):
        data=convo.public(chat["memory"]);data["conversation_id"]=cid;data["title"]=chat["title"];return data
    def summaries(self,session):
        now=time.monotonic()
        return [{"conversation_id":cid,"title":ch["title"],"turn_count":len(ch["memory"]["turns"]),"expired":now-ch["at"]>CHAT_TTL} for cid,ch in session["chats"].items()]
    def do_GET(self):
        if not self.same_host(): return self.fail("host_not_allowed","請使用本機127.0.0.1位址。",403)
        path=urlsplit(self.path);params=parse_qs(path.query)
        if path.path=="/api/scope": return self.reply({"ok":True,**self.server.backend.scope()})
        if path.path in ("/api/conversations","/api/conversation") or path.path.startswith("/api/source/"):
            _,session=self.session()
            if not session:return self.fail("session_required","頁面工作階段已失效，請重新整理。",403)
            try:
                with LOCK:
                    if path.path=="/api/conversations": return self.reply({"ok":True,"conversations":self.summaries(session),"max_conversations":MAX_CHATS})
                    cid=params.get("conversation_id",[""])[0];chat=self.chat(session,cid)
                    if path.path=="/api/conversation": return self.reply({"ok":True,"conversation":self.snapshot(cid,chat)})
                    chunk=params.get("chunk_id",[""])[0];source=path.path.rsplit("/",1)[-1]
                    if chunk not in convo.allowed_ids(chat["memory"]): return self.fail("source_not_issued","請從這個聊天已取得的引用開啟來源。",403)
                src=self.server.backend.source(chunk,full=True)
                if src["source_id"]!=source: raise PublicError("source_not_found","來源與片段不相符。",404)
                return self.reply({"ok":True,"source":src})
            except PublicError as e:return self.fail(e.code,e.message,e.status)
        assets={"/landing.css":("landing.css","text/css; charset=utf-8"),"/landing.js":("landing.js","text/javascript; charset=utf-8"),"/app.js":("app.js","text/javascript; charset=utf-8"),"/app.css":("app.css","text/css; charset=utf-8")}
        # Only explicit local font assets are served; no arbitrary path joins.
        for font in ("font-0.ttf", "font-1.ttf", "font-2.ttf", "font-3.ttf", "font-4.ttf", "font-5.ttf", "noto-serif-tc.ttf"):
            assets["/fonts/"+font]=("fonts/"+font,"font/ttf")
        if path.path in assets:
            name,mime=assets[path.path];data=(WEB/name).read_bytes();self.headers_safe(200,mime,len(data));return self.wfile.write(data)
        if path.path=="/" or path.path in ("/app","/scope","/404","/error") or path.path.startswith(("/chat/","/answer/","/ask/","/source/")):
            sid,_=self.session(create=True)
            if not sid:return self.fail("session_capacity","目前工作階段已滿，請稍後重試。",503)
            data=(WEB/("landing.html" if path.path=="/" else "index.html")).read_bytes();self.headers_safe(200,"text/html; charset=utf-8",len(data),sid);return self.wfile.write(data)
        return self.fail("not_found","找不到這個頁面。",404)
    def read_body(self):
        if self.headers.get("Content-Type","").split(";",1)[0].strip()!="application/json" or self.headers.get("Transfer-Encoding"):raise PublicError("content_type_invalid","查詢必須使用JSON。",415)
        try:size=int(self.headers.get("Content-Length","-1"))
        except ValueError:size=-1
        if not 1<=size<=MAX_BODY:raise PublicError("body_too_large","查詢內容超過限制。",413)
        try:body=json.loads(self.rfile.read(size).decode())
        except (ValueError,UnicodeError):raise PublicError("json_invalid","查詢內容不是有效JSON。")
        if not isinstance(body,dict):raise PublicError("request_fields_invalid","查詢欄位不符合契約。")
        return body
    def do_POST(self):
        if not self.same_host() or self.headers.get("Origin")!=f"http://127.0.0.1:{self.server.server_port}":return self.fail("origin_not_allowed","請從本機介面送出查詢。",403)
        if self.path not in ("/api/query","/api/conversations","/api/conversation/commit","/api/conversation/reset"):return self.fail("not_found","找不到這個端點。",404)
        _,session=self.session()
        if not session:return self.fail("session_required","頁面工作階段已失效，請重新整理。",403)
        try:
            body=self.read_body()
            with LOCK:
                if self.path=="/api/conversations":
                    if body:raise PublicError("request_fields_invalid","新增聊天不接受自訂識別或歷史。")
                    if len(session["chats"])>=MAX_CHATS:raise PublicError("conversation_limit","本次開啟期間最多16個聊天；已保留原聊天，不會自動刪除。",409)
                    cid="chat-"+secrets.token_hex(12);chat={"at":time.monotonic(),"title":"新聊天","memory":convo.new_memory()};session["chats"][cid]=chat
                    return self.reply({"ok":True,"conversation":self.snapshot(cid,chat),"conversations":self.summaries(session)})
                cid=body.get("conversation_id");chat=self.chat(session,cid);memory=chat["memory"]
                if self.path=="/api/conversation/reset":
                    if set(body)!={"conversation_id"}:raise PublicError("request_fields_invalid","清空聊天欄位不符合契約。")
                    chat["memory"]=convo.new_memory();chat["title"]="新聊天"
                    return self.reply({"ok":True,"conversation":self.snapshot(cid,chat),"conversations":self.summaries(session)})
                if self.path=="/api/conversation/commit":
                    if set(body)!={"conversation_id","question_id","commit_token"} or not isinstance(body["commit_token"],str):raise PublicError("request_fields_invalid","確認回合欄位不符合契約。")
                    ok,code=convo.commit(memory,body["commit_token"],body["question_id"])
                    if not ok:raise PublicError(code,"這則回覆未寫入聊天，請保留原問題並重試。",409)
                    if chat["title"]=="新聊天":
                        first=next((t for t in memory["turns"] if t.get("response_kind") in ("normal","in_scope_no_evidence")),None)
                        if first:chat["title"]=first["query_text"][:28]
                    return self.reply({"ok":True,"conversation":self.snapshot(cid,chat),"conversations":self.summaries(session)})
                if set(body)-{"conversation_id","query_text","conditions","clarify_skipped","parent_question_id"}:raise PublicError("request_fields_invalid","查詢欄位不符合契約。")
                clean=validate_query({k:v for k,v in body.items() if k not in ("conversation_id","parent_question_id")})
                if len(memory["turns"])>=convo.MAX_TURNS:raise PublicError("conversation_limit","這個聊天已達20則回覆上限，請新增聊天；原紀錄仍保留。",409)
                parent=body.get("parent_question_id")
                if parent is not None:
                    if not memory["turns"] or memory["turns"][-1]["question_id"]!=parent or memory["turns"][-1]["query_text"]!=clean["query_text"] or not memory["turns"][-1].get("clarify_questions"):
                        raise PublicError("clarification_expired","這則補問已失效，請從目前聊天最後一則繼續。",409)
                plan=convo.plan(clean,memory);generation=memory["generation"];version=memory["version"]
            if not QUERY_GATE.acquire(blocking=False):return self.fail("query_busy","另一筆查詢仍在執行，請稍候再試。",429)
            try:
                if plan["response_kind"]=="normal":
                    result=self.server.backend.query({"query_text":clean["query_text"],"conditions":plan["conditions"],"clarify_skipped":plan["clarify_skipped"]},search_text=plan["search_text"])
                    result["response_kind"]="in_scope_no_evidence" if result["decision"]=="C" and not result["sources"] and not result["clarify_questions"] else "normal"
                    if result["response_kind"]=="in_scope_no_evidence" and not convo.DOMAIN.search(convo.norm(clean["query_text"])) and not plan.get("context_topic"):
                        result.update(response_kind="needs_topic",title="可以補充你想查的主題嗎？",message="這次還沒找到可引用的資料。我主要協助整理論文、口試與離校資訊；如果你想查這些事項，可以補上主題或收件單位，我再幫你核對。",suggestions=convo.SUGGESTIONS[:])
                        plan["response_kind"]="needs_topic"
                else:
                    result={"ok":True,"question_id":"q-"+uuid.uuid4().hex,"query_text":clean["query_text"],"response_kind":plan["response_kind"],"title":plan["title"],"message":plan["message"],"suggestions":plan["suggestions"],"decision":None,"sources":[],"clarify_questions":[],"reason_codes":[],"conflicts":[],"conditions":copy.deepcopy(plan.get("conditions",memory["conditions"])),"clarify_skipped":plan.get("clarify_skipped",memory["skipped"])[:]}
                result["context"]={"previous_question_id":plan.get("context_question_id"),"note":plan.get("context_note"),"intent":plan.get("context_intent"),"pending_conditions":plan.get("pending_conditions",memory.get("pending_conditions",[])),"remembered_conditions":plan.get("conditions",copy.deepcopy(memory["conditions"]))}
                with LOCK:
                    if chat["memory"] is not memory or generation!=memory["generation"] or version!=memory["version"]:raise PublicError("conversation_changed","聊天已更新，這則遲到回覆不會寫入新聊天。",409)
                    token=convo.stage(memory,result,plan)
                return self.reply({**result,"conversation_id":cid,"commit_token":token})
            finally:QUERY_GATE.release()
        except PublicError as e:self.fail(e.code,e.message,e.status)
        except Exception:self.fail("internal_error","本機服務發生錯誤，請稍後重試。",500)

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=8767);args=parser.parse_args()
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler);server.backend=VectorBackend();print(f"http://127.0.0.1:{server.server_port}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=="__main__":main()
