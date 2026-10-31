"""Read-only desktop adapter: pinned local BGE/lexical RRF, no generation API."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from flow_summary import build_flow
from scope_aliases import canonical_dept, EVIDENCE as DEPT_ALIAS_EVIDENCE

PROJECT_ROOT = Path(os.environ.get("CAMPUS_RAG_ROOT", "./local-data")).resolve()
CORPUS_ROOT = PROJECT_ROOT / "原型/離線檢索_v0.6"
CLI = CORPUS_ROOT / "src/app.py"
ENGINE_PYTHON = Path(os.environ.get("CAMPUS_RAG_PYTHON", sys.executable))
SKIP_SLOTS = {"enrollment_year", "target_academic_term", "dept", "degree_level"}
CONDITION_KEYS = {"enrollment_year", "target_academic_term", "dept", "degree_level"}
PACKAGED_SERVICE_SHA = "3c80234443281ad9bedbf80170d7beab7c39f7e2b25b68834bdfe48572aed382"
ENGINE_INFO = {"id": "v0.6-lexical", "label": "v0.6 詞彙檢索基線", "generative": False}


class PublicError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def _year(value):
    return value is None or (type(value) is int and 1 <= value <= 999)


def validate_query(body: object) -> dict:
    if not isinstance(body, dict) or set(body) - {"query_text", "conditions", "clarify_skipped"}:
        raise PublicError("request_fields_invalid", "查詢欄位不符合介面契約，請重新送出。")
    query = body.get("query_text")
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise PublicError("query_invalid", "請輸入 1–200 字的問題。")
    cond = body.get("conditions", {})
    if not isinstance(cond, dict) or set(cond) - CONDITION_KEYS:
        raise PublicError("conditions_invalid", "條件欄位不符合契約。")
    if "degree_level" in cond and cond["degree_level"] not in ("碩士", "博士"):
        raise PublicError("degree_invalid", "學位別請選碩士或博士；不確定可略過。")
    if "dept" in cond and (not isinstance(cond["dept"], str) or not cond["dept"].strip() or len(cond["dept"]) > 80):
        raise PublicError("dept_invalid", "系所請輸入 1–80 字。")
    if "enrollment_year" in cond and not _year(cond["enrollment_year"]):
        raise PublicError("year_invalid", "入學學年度須為民國整數學年度，或未知。")
    if "target_academic_term" in cond:
        term = cond["target_academic_term"]
        if term is not None and (not isinstance(term, dict) or set(term) != {"year", "semester"} or not _year(term["year"]) or (term["semester"] is not None and (type(term["semester"]) is not int or term["semester"] not in (1, 2)))):
            raise PublicError("term_invalid", "目標學期欄位不符合契約。")
    skipped = body.get("clarify_skipped", [])
    if not isinstance(skipped, list) or any(not isinstance(s, str) or s not in SKIP_SLOTS for s in skipped) or len(skipped) != len(set(skipped)):
        raise PublicError("skip_invalid", "略過條件不符合契約。")
    if set(cond) & set(skipped):
        raise PublicError("condition_skip_conflict", "同一條件不能同時填答與略過。")
    return {"query_text": query, "conditions": cond, "clarify_skipped": skipped}


class LexicalBackend:
    engine_info = ENGINE_INFO
    def __init__(self):
        # Use the accepted loader, including its content hash and schema checks.
        sys.path.insert(0, str(CORPUS_ROOT / "src"))
        from corpus import Corpus
        self.corpus = Corpus(CORPUS_ROOT)
        self.sources = {s["source_id"]: s for s in self.corpus.manifest["sources"]}
        self.urls = {}
        with (PROJECT_ROOT / "資料盤點/第一批/來源台帳.csv").open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                url = row.get("url", "")
                if url.startswith("https://"):
                    self.urls[row["source_id"]] = url
        self.corpus_hash = hashlib.sha256((CORPUS_ROOT / "chunks.jsonl").read_bytes()).hexdigest()

    def source(self, chunk_id: str, full: bool = False) -> dict:
        c = self.corpus.by_id.get(chunk_id)
        if not c:
            raise PublicError("source_not_found", "找不到這筆來源。", 404)
        s = self.sources[c["source_id"]]
        ap = c.get("applies") or {}
        d = {"chunk_id": chunk_id, "source_id": c["source_id"], "title": c["doc_title"],
             "unit": s.get("authority_unit"), "source_type": s.get("source_type"),
             "official": s.get("is_official"), "document_date": s.get("official_date"),
             "date_kind": s.get("date_kind"), "observed_at": s.get("observed_at"),
             "page_pdf": c.get("page_pdf"), "page_end": c.get("page_end"),
             "page_kind": c.get("page_kind"), "section": c.get("section"),
             "char_offset": c.get("char_offset"), "quote_anchor": c.get("quote_anchor"),
             "applicability_status": ap.get("applicability_status", "unknown"),
             "scope": ap.get("scope") or [], "degree_level": ap.get("degree_level") or [],
             "limit_note": c.get("limit_note"), "url": self.urls.get(c["source_id"])}
        if full:
            d["text"] = c["text"]
            d["text_sha256"] = hashlib.sha256(c["text"].encode()).hexdigest()
        return d

    def scope(self) -> dict:
        return {"engine": self.engine_info, "corpus_hash": self.corpus_hash,
                "source_count": len(self.sources), "chunk_count": len(self.corpus.chunks),
                "data_observed_at": max((s.get("observed_at", "") for s in self.sources.values()), default="未標示"),
                "source_titles": [{"source_id": s["source_id"], "title": s["title"]} for s in self.sources.values()],
                "coverage": "研究生論文繳交、格式、學位考試與離校共通規定；工科系為單一系所樣本。",
                "limitations": ["本原型以既有文件回傳檢索片段，未串接生成式回答 API。", "文件年度語意與個人適用性尚未核實；不代校方裁定。", "不涵蓋其他系所全部規定、個人成績、帳號或官方個人進度。", "每個聊天各自保留本次開啟期間的對話與明填條件；只在可確認的短追問中接續前題，不跨聊天、也不寫入磁碟。"]}

    def query(self, body: dict, *, search_text: str | None = None) -> dict:
        clean = validate_query(body)
        qid = "q-" + uuid.uuid4().hex
        req = {"question_id": qid, "query_schema_version": "2", "query_text": clean["query_text"]}
        cond = clean["conditions"]
        for key in ("enrollment_year", "target_academic_term"):
            if key in cond:
                req[key] = cond[key]
        payload = {"runtime": {"message_id": "m-" + uuid.uuid4().hex, "event_seq": 1,
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   "request": req, "clarify_skipped": clean["clarify_skipped"]}
        for key in ("degree_level", "dept"):
            if key in cond:
                payload[key] = canonical_dept(cond[key]) if key == "dept" else cond[key]
        if search_text is not None:
            if not isinstance(search_text, str) or not 1 <= len(search_text) <= 200:
                raise PublicError("context_query_invalid", "請補充一個200字內的完整問題。")
            payload["search_text"] = search_text
        raw = self._execute(payload)
        dec = raw.get("decision") or {}
        if dec.get("decision") not in ("A", "B", "C", "reference_only"):
            raise PublicError("decision_invalid", "檢索結果缺少有效決策。", 502)
        items = (dec.get("answer") or {}).get("items") or []
        ids = [i.get("chunk_id") for i in items]
        if any(cid not in self.corpus.by_id for cid in ids) or len(ids) != len(set(ids)):
            raise PublicError("citation_invalid", "來源對應驗證未通過，請重新查詢。", 502)
        sources = [self.source(cid) for cid in ids]
        questions = dec.get("clarify_questions") or []
        if len(questions) > 3:
            raise PublicError("clarification_invalid", "補問數量超過介面契約。", 502)
        response = {"ok": True, "question_id": qid, "query_text": clean["query_text"],
                "engine": self.engine_info, "corpus_hash": self.corpus_hash,
                "decision": dec["decision"], "summary": (dec.get("answer") or {}).get("summary", ""),
                "reason_codes": dec.get("reason_codes") or [], "clarify_questions": questions,
                "conflicts": dec.get("conflicts") or [], "conditions": cond,
                "clarify_skipped": clean["clarify_skipped"], "sources": sources}
        response["flow"] = build_flow(search_text or clean["query_text"], response, self.corpus,
                                      excluded_ids=[x["chunk_id"] for x in dec.get("excluded", [])])
        # The public summary must describe the answer actually rendered. The
        # unchanged engine decision/reasons remain available for applicability.
        response["summary"] = response["flow"]["summary"]["text"]
        if "dept" in cond and canonical_dept(cond["dept"]) != cond["dept"]:
            response["condition_resolution"] = {"dept": {"submitted": cond["dept"], "source_name": canonical_dept(cond["dept"]), "basis": DEPT_ALIAS_EVIDENCE}}
        return response

    def _execute(self, payload):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        try:
            p = subprocess.run([str(ENGINE_PYTHON), str(CLI), "--input", json.dumps(payload, ensure_ascii=False)],
                               capture_output=True, text=True, timeout=35, cwd=str(PROJECT_ROOT), env=env)
        except subprocess.TimeoutExpired:
            raise PublicError("query_timeout", "查詢逾時，請稍後重試。", 504)
        except OSError:
            raise PublicError("engine_unavailable", "本機檢索服務目前無法啟動。", 503)
        try:
            raw = json.loads(p.stdout)
        except (ValueError, TypeError):
            raise PublicError("engine_response_invalid", "檢索服務未回傳有效結果。", 502)
        if not raw.get("ok"):
            code = (raw.get("error") or {}).get("code", "engine_failed")
            raise PublicError(code, "檢索服務無法完成這次查詢，請檢查服務後重試。", 503 if p.returncode in (4, 5) else 400)
        return raw


class VectorBackend(LexicalBackend):
    """Reuse request validation/source DTO; a single lazy B2 model instance.

    A failed engine stays unavailable until restart. There is no lexical fallback.
    All internal retrieval traces remain server-side.
    """
    def __init__(self):
        super().__init__()
        self.engine_info = {"id": "bge-lexical-rrf-b2", "route": "B2",
                            "label": "本機混合檢索・來源查證", "generative": False,
                            "state": "not_loaded"}
        self._vector = None
        self._engine_error = None

    def scope(self):
        result = super().scope()
        result["limitations"][0] = "以本機 BGE 向量與詞彙檢索融合查找文件；回傳原文，未使用生成式回答 API。"
        result["limitations"].append("摘要由有限的人工策展條文模板整理，逐項核對本次命中的原文；不是生成式 AI 回答。")
        result["limitations"].append("語意近鄰本身不代表有答案；目前缺少詞彙依據時會保守拒答。")
        result["limitations"].append("停止等待只中斷頁面等候，本機查詢可能仍在完成；期間重新送出可能顯示服務忙碌。")
        return result

    def _execute(self, payload):
        if self._engine_error:
            raise PublicError("vector_engine_unavailable", "本機向量檢索未能載入，請檢查服務並重新啟動。", 503)
        if self._vector is None:
            try:
                path = Path(__file__).parent / "engine/vector_service.py"
                expected = PACKAGED_SERVICE_SHA
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise RuntimeError("vector_service_integrity_failure")
                from engine.vector_service import VectorRetriever
                self._vector = VectorRetriever(PROJECT_ROOT, route="B2")
                self.engine_info["state"] = "ready"
            except Exception as exc:
                self._engine_error = type(exc).__name__
                self.engine_info["state"] = "unavailable"
                raise PublicError("vector_engine_unavailable", "本機向量檢索未能載入，請檢查服務並重新啟動。", 503)
        try:
            return self._vector.answer(payload)
        except Exception as exc:
            code = getattr(exc, "code", "vector_query_failed")
            if not isinstance(code, str) or not code.replace("_", "").isalnum():
                code = "vector_query_failed"
            invalid = {"query_invalid", "query_over_512_tokens", "query_schema_version_required", "unsupported_query_schema_version"}
            if code == "normalization_timeout":
                raise PublicError(code, "條件處理逾時，請稍後重試。", 504)
            raise PublicError(code, "本機向量檢索無法完成這次查詢，請檢查服務後重試。", 400 if code in invalid else 503)
