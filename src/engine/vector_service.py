"""Persistent local BGE + Chroma retrieval, with the accepted v0.6 decision path.

No LLM, answer generation, model API, corpus write, or policy inference.
Run this module inside the existing BGE_B3 Python environment. The sibling
retrieval runner supplies frozen artifact identity checks and lexical/RRF logic.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import threading
import time

_RUNNER = Path(__file__).resolve().with_name("frozen_runner.py")
_RUNNER_SHA = "7d6aed5171c8656409a1456a5c3bbd6f36c102f8840c0b67629e6a500aa6aa41"


def _load_runner():
    if hashlib.sha256(_RUNNER.read_bytes()).hexdigest() != _RUNNER_SHA:
        raise RuntimeError("frozen_runner_integrity_failure")
    spec = importlib.util.spec_from_file_location("campus_rag_frozen_retrieval", _RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("frozen_runner_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VectorServiceError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(code + (": " + detail if detail else ""))


class VectorRetriever:
    """One model load per instance; .search returns v0.6-compatible raw hits.

    `backend = VectorRetriever()` can live on the loopback UI server object.
    The existing server's bounded semaphore ensures only one query at once;
    this class also holds a lock so use outside that server stays serialized.
    """

    def __init__(self, project_root=None, route="B2"):
        if route not in {"B0", "B1", "B2"}:
            raise VectorServiceError("route_invalid")
        self.root = Path(project_root or os.environ.get("CAMPUS_RAG_ROOT", "./local-data")).resolve()
        self.runner = _load_runner()
        self.route = route
        self.lock = threading.Lock()
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        self.runner.install_outbound_guard()
        self.ctx = self.runner.preflight(self.root)  # DB is opened only from a /tmp copy
        self._load_model()
        self._load_corpus_and_decider()

    def _rss_bytes(self):
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss if sys.platform == "darwin" else rss * 1024

    def _health(self):
        if self.runner.OUTBOUND_ATTEMPTS:
            raise VectorServiceError("outbound_attempted", repr(self.runner.OUTBOUND_ATTEMPTS))
        if self._rss_bytes() > 8 * 2**30:
            raise VectorServiceError("rss_limit_exceeded", str(self._rss_bytes()))

    def _load_model(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        model_dir = self.root / "實驗/向量_BGE_B1/model"
        t = time.perf_counter()
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True,
                                                        trust_remote_code=False)
        self.model = AutoModel.from_pretrained(model_dir, local_files_only=True,
                                                trust_remote_code=False, weights_only=True,
                                                torch_dtype=torch.float32)
        self.model.eval().cpu()
        self.init_model_seconds = time.perf_counter() - t
        self._health()

    def _load_corpus_and_decider(self):
        src = self.root / "原型/離線檢索_v0.6/src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        import adapter
        import decide6
        import service
        from corpus import Corpus

        self.adapter, self.decide6, self.service = adapter, decide6, service
        self.corpus = Corpus(src.parent)
        if set(self.corpus.by_id) != {c["chunk_id"] for c in self.ctx["chunks"]}:
            raise VectorServiceError("corpus_loader_mismatch")
        self._health()

    def _embed(self, text: str):
        import numpy as np
        import torch

        encoded = self.tokenizer(self.runner.QUERY_INSTRUCTION + text,
                                 add_special_tokens=True, truncation=False, return_tensors="pt")
        tokens = int(encoded["attention_mask"].sum().item())
        if tokens > 512:
            raise VectorServiceError("query_over_512_tokens", str(tokens))
        with torch.inference_mode():
            out = self.model(**encoded)
            pooled = torch.nn.functional.normalize(out.last_hidden_state[:, 0, :], p=2, dim=1)
        vec = pooled[0].cpu().numpy().astype(np.float32)
        if vec.shape != (1024,) or not np.isfinite(vec).all() or abs(float(np.linalg.norm(vec)) - 1) >= 1e-5:
            raise VectorServiceError("query_vector_invalid")
        self._health()
        return vec, tokens

    def search_with_trace(self, text: str, route: str | None = None):
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise VectorServiceError("query_invalid")
        use = route or self.route
        if use not in {"B0", "B1", "B2"}:
            raise VectorServiceError("route_invalid")
        with self.lock:
            self._health()
            start = time.perf_counter()
            b0, lexical = self.runner.lexical(text, self.ctx["chunks"], self.ctx["candidates"],
                                              self.root / "原型/離線檢索_v0.6/src")
            b0_done = time.perf_counter()
            vec, tokens = self._embed(text)
            encoded_done = time.perf_counter()
            result = self.ctx["col"].query(query_embeddings=[vec.tolist()], n_results=28,
                                             include=["distances"])
            dense = [{"chunk_id": cid, "distance": float(d)}
                     for cid, d in zip(result["ids"][0], result["distances"][0])]
            if len(dense) != 28 or {d["chunk_id"] for d in dense} != self.ctx["candidates"]:
                raise VectorServiceError("dense_candidates_invalid")
            import math
            if not all(math.isfinite(d["distance"]) for d in dense):
                raise VectorServiceError("dense_distance_invalid")
            dense.sort(key=lambda item: (item["distance"], item["chunk_id"]))
            b1 = [d["chunk_id"] for d in dense[:self.runner.DEPTH]]
            fusion = self.runner.rrf(b0, b1)
            b2 = [x["chunk_id"] for x in fusion]
            orders = {"B0": b0, "B1": b1, "B2": b2}
            scores = {
                "B0": {x["chunk_id"]: float(x["score"]) for x in lexical["ranked"]},
                "B1": {x["chunk_id"]: 1.0-x["distance"] for x in dense},
                "B2": {x["chunk_id"]: x["score"] for x in fusion},
            }
            chosen = orders[use]
            if len(chosen) != len(set(chosen)) or not set(chosen) <= self.ctx["candidates"]:
                raise VectorServiceError("ranking_invalid")
            hits = [{"chunk": self.corpus.by_id[cid], "score": scores[use][cid]}
                    for cid in chosen]
            self._health()
            trace = {"route": use, "tokens": tokens, "B0": lexical, "B1": dense, "B2": fusion,
                     "rankings": orders, "timing_seconds": {
                         "lexical": b0_done-start, "encode": encoded_done-b0_done,
                         "chroma_query_and_fusion": time.perf_counter()-encoded_done},
                     "model_loaded_once": True, "outbound_attempts": list(self.runner.OUTBOUND_ATTEMPTS)}
            return hits, trace

    def search(self, query: str, route: str | None = None):
        """Return `[{chunk: original_chunk_dict, score: raw_route_score}, ...]`."""
        return self.search_with_trace(query, route)[0]

    def _normalize_bounded(self, request: dict, runtime: dict | None,
                           prior=None, history=None):
        """Same v0.5 CLI contract as v0.6 service; bound its transport to 15s.

        The frozen service's subprocess.run has no timeout. The validator and
        normalizer still execute in that unchanged v0.5 CLI, including all
        route/L1/L2/L3 checks. No local substitute normalization is attempted.
        """
        original = self.service
        if "context" in request:
            raise original.ServiceError("request_must_not_carry_context")
        if not isinstance(runtime, dict) or not runtime.get("message_id") or not runtime.get("event_seq"):
            raise original.ServiceError("runtime_context_required")
        if not os.path.exists(original.V05_CLI):
            raise original.ServiceError("normalize_service_missing", 4, original.V05_CLI)
        with tempfile.TemporaryDirectory() as directory:
            args = [sys.executable, original.V05_CLI,
                    "--request", original._write(directory, "request.json", request),
                    "--context", original._write(directory, "context.json", runtime)]
            if prior is not None:
                args += ["--prior", original._write(directory, "prior.json", prior)]
            if history is not None:
                args += ["--history", original._write(directory, "history.json", history)]
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired as exc:
                raise VectorServiceError("normalization_timeout", "v0.5 CLI exceeded 15 seconds") from exc
        try:
            output = json.loads(proc.stdout.strip() or "{}")
        except json.JSONDecodeError:
            raise original.ServiceError("normalize_service_bad_output", 5, proc.stdout[:200])
        if not output.get("ok"):
            raise original.ServiceError(output.get("code") or "normalize_failed", proc.returncode or 3,
                                        output.get("message", ""))
        return output["normalized"]

    def answer(self, payload: dict, route: str | None = None):
        """Use actual v0.5 validation, v0.6 adapter and decide6 on raw hits.

        Payload is the same trusted envelope as v0.6 app.run: runtime is server
        supplied, request must have schema version 2, and prior/history optional.
        No new applicability inference is introduced here.
        """
        if not isinstance(payload, dict):
            raise VectorServiceError("payload_invalid")
        request, runtime = payload.get("request"), payload.get("runtime")
        if not isinstance(request, dict):
            raise VectorServiceError("request_required")
        qsv = request.get("query_schema_version")
        if qsv is None:
            raise VectorServiceError("query_schema_version_required")
        if str(qsv) != "2":
            raise VectorServiceError("unsupported_query_schema_version")
        normalized = self._normalize_bounded(request, runtime,
                                             payload.get("prior"), payload.get("history"))
        envelope = {"question_id": request.get("question_id"),
                    "message_id": (runtime or {}).get("message_id"),
                    "query_text": request.get("query_text"),
                    "search_text": payload.get("search_text"),
                    "clarify_skipped": payload.get("clarify_skipped") or [],
                    "dept": payload.get("dept"), "degree_level": payload.get("degree_level")}
        retrieval = self.adapter.to_retrieval(normalized, envelope)
        hits, trace = self.search_with_trace(retrieval["search_text"], route)
        # UI policy guard: BGE always returns nearest neighbors, even when no
        # source directly supports the words in the question. Until a dense
        # answerability threshold is calibrated, do not feed those neighbors
        # into an answer decision when the frozen lexical route found nothing.
        # Raw B0/B1/B2 rankings remain in trace for inspection and benchmarks.
        direct_support = bool(trace["rankings"]["B0"])
        trace["decision_gate"] = {
            "lexical_direct_support": direct_support,
            "dense_answerability_calibrated": False,
            "policy": "empty_decision_hits_when_b0_empty",
        }
        if not direct_support:
            hits = []
        # Match the accepted v0.6 retrieval boundary (DEFAULT_LIMIT=5).
        # decide6 may expand a conflict counterpart from these five; feeding
        # all 20 raw nearest neighbors would introduce unrelated conflict
        # groups and crowd genuinely relevant items out of the answer.
        hits = hits[:5]
        trace["decision_input_limit"] = 5
        trace["decision_input_chunk_ids"] = [h["chunk"]["chunk_id"] for h in hits]
        hits = self.decide6.locate_hits(self.corpus, hits)
        decision = self.decide6.decide(self.corpus, retrieval, hits)
        retrieval["document_conflicts"] = decision.get("conflicts") or []
        return {"ok": True, "question_id": request.get("question_id"), "normalized": normalized,
                "retrieval": retrieval, "hits": [{"chunk_id": h["chunk"]["chunk_id"],
                 "score": h["score"]} for h in hits[:5]], "decision": decision,
                "route": route or self.route, "retrieval_trace": trace}
