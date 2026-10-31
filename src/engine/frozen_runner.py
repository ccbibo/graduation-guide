#!/usr/bin/env python3
"""Frozen 21-query B0/B1/B2 retrieval experiment. No answer generation.

The benchmark runs only with --run --allow-benchmark --out-dir NEW_DIRECTORY.
--validate and --self-test never encode a query or write the project database.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import pathlib
import resource
import shutil
import socket
import sys
import tempfile
import time

GOLD_SHA = "2dce1b4256319b5d337e54317c721be5c6b72e8d4caeb43c5132f692cb429702"
CORPUS_SHA = "de8bd7390497f77bc278877781613ab1cab2fca9475ac22835cb4306eb6227fb"
NPZ_SHA = "f7c9ed77e52da380561c618191abf048d456c945303cb14ac59c2fdb27738ec3"
WEIGHT_SHA = "bf84a56fb045c24e090495195584a3922c3e4204107f0bba8b79c11f67a207f2"
TOKENIZER_SHA = "7dfbf1966ebf99d471c3796e9b457329d2b2182b817e144f1e904b957745c839"
REVISION = "79e7739b6ab944e86d6171e44d24c997fc1e0116"
EMBEDDING_ID = "BAAI/bge-large-zh-v1.5@" + REVISION[:12]
COLLECTION = "rag-de8bd7390497-dcb76a2168b9"
INDEX_KEY = "dcb76a2168b9"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
DEPTH = 20
RRF_K = 60
MAX_TOKENS = 512
OUTBOUND_ATTEMPTS: list[dict] = []


class CheckError(Exception):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CheckError(code)


def install_outbound_guard() -> None:
    """Process-level guard, not a packet-level proof; allows loopback only."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection
    original_resolve = socket.getaddrinfo

    def local(address) -> bool:
        if isinstance(address, str):  # local Unix socket path
            return address.startswith("/")
        if not isinstance(address, tuple) or not address:
            return False
        host = address[0]
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
        except ValueError:
            return False

    def blocked(kind, address):
        OUTBOUND_ATTEMPTS.append({"api": kind, "address": repr(address)})
        raise OSError("outbound_blocked")

    def connect(sock, address):
        return original_connect(sock, address) if local(address) else blocked("connect", address)

    def connect_ex(sock, address):
        return original_connect_ex(sock, address) if local(address) else blocked("connect_ex", address)

    def create(address, *args, **kwargs):
        return original_create(address, *args, **kwargs) if local(address) else blocked("create_connection", address)

    def resolve(host, *args, **kwargs):
        if host is None or local((host, 0)):
            return original_resolve(host, *args, **kwargs)
        return blocked("getaddrinfo", host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create
    socket.getaddrinfo = resolve


def paths(root: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        "gold": root / "資料盤點/第一批/gold_v2.json",
        "corpus": root / "原型/離線檢索_v0.6/chunks.jsonl",
        "npz": root / "實驗/向量_BGE_B4/full_vectors.npz",
        "model": root / "實驗/向量_BGE_B1/model",
        "db": root / "實驗/向量_正式比較/chroma",
        "src": root / "原型/離線檢索_v0.6/src",
    }


def preflight(root: pathlib.Path):
    """Check actual bytes, IDs, collection identity, and all stored vectors."""
    import numpy as np
    import chromadb
    from chromadb.config import Settings

    p = paths(root)
    expected = {"gold": GOLD_SHA, "corpus": CORPUS_SHA, "npz": NPZ_SHA}
    for key, want in expected.items():
        require(sha256(p[key]) == want, key + "_sha_mismatch")
    require(sha256(p["model"] / "pytorch_model.bin") == WEIGHT_SHA, "weight_sha_mismatch")
    require(sha256(p["model"] / "tokenizer.json") == TOKENIZER_SHA, "tokenizer_sha_mismatch")
    gold = json.loads(p["gold"].read_text(encoding="utf-8"))
    questions = gold["questions"]
    require(len(questions) == 21 and len({q["question_id"] for q in questions}) == 21, "gold_questions_invalid")
    require(sum(q.get("is_primary") is True for q in questions) == 10, "gold_primary_invalid")
    require(all(isinstance(q.get("question_original"), str) and q["question_original"] for q in questions), "gold_query_missing")
    chunks = [json.loads(line) for line in p["corpus"].read_text(encoding="utf-8").splitlines()]
    ids = [c["chunk_id"] for c in chunks]
    containers = {c["parent_chunk_id"] for c in chunks if c.get("parent_chunk_id")}
    candidates = set(ids) - containers
    require(len(chunks) == 31 and len(set(ids)) == 31 and len(containers) == 3 and len(candidates) == 28, "candidate_set_invalid")
    require(candidates == set(gold["corpus"]["candidate_chunk_ids"]), "gold_candidates_mismatch")
    data = np.load(p["npz"], allow_pickle=False)
    vectors, vector_ids = data["vectors"], data["chunk_ids"].tolist()
    require(vectors.shape == (31, 1024) and vectors.dtype == np.float32, "npz_shape_invalid")
    require(vector_ids == ids and np.isfinite(vectors).all(), "npz_ids_or_values_invalid")
    require(bool(np.all(np.abs(np.linalg.norm(vectors, axis=1) - 1) < 1e-5)), "npz_norm_invalid")
    by_vector = dict(zip(vector_ids, vectors))
    # Chroma opens SQLite in write mode even for get/query; isolate all runtime writes.
    temporary_db = tempfile.TemporaryDirectory(prefix="rag_benchmark_readcopy_")
    copied_db = pathlib.Path(temporary_db.name) / "chroma"
    shutil.copytree(p["db"], copied_db)
    client = chromadb.PersistentClient(path=str(copied_db), settings=Settings(anonymized_telemetry=False))
    require(COLLECTION in [c.name for c in client.list_collections()], "collection_missing")
    col = client.get_collection(COLLECTION, embedding_function=None)
    m = col.metadata or {}
    expected_meta = {
        "hnsw:space": "cosine", "corpus_hash": CORPUS_SHA, "corpus_hash12": CORPUS_SHA[:12],
        "index_key12": INDEX_KEY, "candidate_set": "p28", "chunk_count": 28,
        "embedding_id": EMBEDDING_ID, "model_revision": REVISION, "model_weights_sha256": WEIGHT_SHA,
        "dim": 1024, "dtype": "float32", "norm": "l2", "metric": "cosine", "schema_ver": "s1",
    }
    require(all(m.get(k) == v for k, v in expected_meta.items()), "collection_identity_mismatch")
    require(col.count() == 28, "collection_count_mismatch")
    got = col.get(include=["embeddings", "metadatas", "documents"])
    require(len(got["ids"]) == 28 and set(got["ids"]) == candidates, "collection_ids_mismatch")
    require(all(doc is None for doc in got["documents"]), "collection_documents_present")
    by_chunk = dict(zip(ids, chunks))
    for cid, vec, meta in zip(got["ids"], got["embeddings"], got["metadatas"]):
        chunk = by_chunk[cid]
        require(bool(np.allclose(vec, by_vector[cid], rtol=0, atol=1e-7)), "collection_vector_mismatch")
        require(meta.get("chunk_id") == cid and meta.get("embedding_id") == EMBEDDING_ID, "chunk_metadata_mismatch")
        require(meta.get("text_sha256") == hashlib.sha256(chunk["text"].encode()).hexdigest(), "chunk_text_mismatch")
        require(meta.get("citation_key") == "|".join([chunk["source_id"], cid.split("#")[1][:12],
                chunk["span_key"], meta.get("extractor_ver", ""), cid.split("#")[2]]), "citation_key_mismatch")
        require(("page_pdf" not in meta) if chunk.get("page_pdf") is None else meta.get("page_pdf") == chunk["page_pdf"], "page_pdf_mismatch")
    require(not OUTBOUND_ATTEMPTS, "outbound_attempted")
    return {"gold": gold, "chunks": chunks, "candidates": candidates, "col": col,
            "temporary_db": temporary_db, "db_copy_path": str(copied_db),
            "identity": {"gold_sha256": GOLD_SHA, "corpus_sha256": CORPUS_SHA, "npz_sha256": NPZ_SHA,
                         "weights_sha256": WEIGHT_SHA, "tokenizer_sha256": TOKENIZER_SHA,
                         "model_revision": REVISION, "embedding_id": EMBEDDING_ID,
                         "collection": COLLECTION, "index_key12": INDEX_KEY}}


def lexical(question: str, chunks: list[dict], candidates: set[str], src: pathlib.Path):
    sys.path.insert(0, str(src))
    from retrieve import MIN_SCORE, query_terms, score_chunk

    require(MIN_SCORE == 3, "lexical_threshold_changed")
    terms = query_terms(question)
    raw = []
    for chunk in chunks:
        cid = chunk["chunk_id"]
        if cid in candidates:
            score, matched = score_chunk(chunk, terms)
            if score >= MIN_SCORE:
                raw.append({"chunk_id": cid, "score": score, "matched_terms": matched})
    raw.sort(key=lambda x: (-x["score"], x["chunk_id"]))
    return [x["chunk_id"] for x in raw[:DEPTH]], {"terms": terms, "min_score": MIN_SCORE, "ranked": raw}


def rrf(a: list[str], b: list[str]):
    scores: dict[str, float] = {}
    contributions: dict[str, list[dict]] = {}
    for route, ids in (("B0", a), ("B1", b)):
        for rank, cid in enumerate(dict.fromkeys(ids[:DEPTH]), 1):
            contribution = 1 / (RRF_K + rank)
            scores[cid] = scores.get(cid, 0.0) + contribution
            contributions.setdefault(cid, []).append({"route": route, "rank": rank, "value": contribution})
    order = sorted(scores, key=lambda cid: (-scores[cid], cid))[:DEPTH]
    return [{"chunk_id": cid, "score": scores[cid], "contributions": contributions[cid]} for cid in order]


def self_test():
    fused = rrf(["a", "b", "a"], ["b", "a", "c"])
    require([x["chunk_id"] for x in fused] == ["a", "b", "c"], "rrf_tie_or_dedup_invalid")
    require(len(rrf([str(i) for i in range(25)], [])) == DEPTH, "rrf_depth_invalid")
    # Test the guard on a non-network path without a socket connection.
    require(not OUTBOUND_ATTEMPTS, "unexpected_outbound")
    return {"ok": True, "tests": ["rrf_tie", "rrf_dedup", "rrf_depth", "no_outbound_attempt"]}


def benchmark(ctx, root: pathlib.Path, outdir: pathlib.Path):
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel

    require(not outdir.exists(), "out_dir_exists")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    p = paths(root)
    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(p["model"], local_files_only=True, trust_remote_code=False)
    model = AutoModel.from_pretrained(p["model"], local_files_only=True, trust_remote_code=False,
                                      weights_only=True, torch_dtype=torch.float32)
    model.eval().cpu()
    t_loaded = time.perf_counter()
    require(not OUTBOUND_ATTEMPTS, "outbound_attempted")
    rankings = {"B0": {}, "B1": {}, "B2": {}}
    details = {}
    query_vectors = []
    qids = []
    for q in ctx["gold"]["questions"]:
        qid, text = q["question_id"], q["question_original"]
        start = time.perf_counter()
        b0, lexical_detail = lexical(text, ctx["chunks"], ctx["candidates"], p["src"])
        lexical_done = time.perf_counter()
        encoded = tokenizer(QUERY_INSTRUCTION + text, add_special_tokens=True,
                            truncation=False, return_tensors="pt")
        token_count = int(encoded["attention_mask"].sum())
        require(token_count <= MAX_TOKENS, "query_over_512_tokens:" + qid)
        with torch.inference_mode():
            output = model(**encoded)
            vector = torch.nn.functional.normalize(output.last_hidden_state[:, 0, :], p=2, dim=1)
        v = vector[0].cpu().numpy().astype(np.float32)
        require(v.shape == (1024,) and np.isfinite(v).all() and abs(float(np.linalg.norm(v)) - 1) < 1e-5,
                "query_vector_invalid:" + qid)
        encoded_done = time.perf_counter()
        got = ctx["col"].query(query_embeddings=[v.tolist()], n_results=28, include=["distances"])
        dense = [{"chunk_id": cid, "distance": float(dist)} for cid, dist in zip(got["ids"][0], got["distances"][0])]
        require(len(dense) == 28 and {x["chunk_id"] for x in dense} == ctx["candidates"], "dense_candidates_invalid:" + qid)
        require(all(math.isfinite(x["distance"]) for x in dense), "dense_distance_invalid:" + qid)
        dense.sort(key=lambda x: (x["distance"], x["chunk_id"]))
        b1 = [x["chunk_id"] for x in dense[:DEPTH]]
        query_done = time.perf_counter()
        fusion = rrf(b0, b1)
        b2 = [x["chunk_id"] for x in fusion]
        for name, ids in (("B0", b0), ("B1", b1), ("B2", b2)):
            require(len(ids) == len(set(ids)) and set(ids) <= ctx["candidates"], "ranking_invalid:" + name + ":" + qid)
            rankings[name][qid] = ids
        details[qid] = {"question_original": text, "tokens": token_count,
                        "B0": lexical_detail, "B1": {"ranked_all_28": dense}, "B2": fusion,
                        "seconds": {"lexical": lexical_done-start, "encode": encoded_done-lexical_done,
                                    "query": query_done-encoded_done, "total": time.perf_counter()-start}}
        query_vectors.append(v)
        qids.append(qid)
        require(not OUTBOUND_ATTEMPTS, "outbound_attempted")
    require(len(qids) == 21 and not OUTBOUND_ATTEMPTS, "benchmark_incomplete_or_outbound")
    # Fresh, exclusive directory. No gold, corpus, or database write is performed.
    outdir.mkdir(parents=False, exist_ok=False)
    np.savez_compressed(outdir / "query_vectors.npz", vectors=np.stack(query_vectors), question_ids=np.array(qids))
    (outdir / "rankings.json").write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {**ctx["identity"], "runner_sha256": sha256(pathlib.Path(__file__)),
                "parameterization": {"candidate_count": 28, "depth": DEPTH, "lexical_min_score": 3,
                                     "lexical_tie": "(-score,chunk_id)", "dense_metric": "cosine_distance_1_minus_cosine",
                                     "dense_tie": "(distance,chunk_id)", "rrf_k": RRF_K,
                                     "rrf_tie": "(-score,chunk_id)", "instruction": QUERY_INSTRUCTION,
                                     "query_tokens_max": MAX_TOKENS, "threads": 2, "dtype": "float32",
                                     "pooling": "cls", "normalization": "l2", "no_metadata_filter": True},
                "questions": qids, "timing_seconds": {"cold_model_load": t_loaded-t0,
                    "encode_total": sum(x["seconds"]["encode"] for x in details.values()),
                    "query_total": sum(x["seconds"]["query"] for x in details.values()),
                    "lexical_total": sum(x["seconds"]["lexical"] for x in details.values()),
                    "benchmark_total": time.perf_counter()-t0},
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "outbound_guard": {"scope": "python_socket_process_level", "attempts": OUTBOUND_ATTEMPTS},
                "outputs_sha256": {name: sha256(outdir/name) for name in
                                   ("rankings.json", "details.json", "query_vectors.npz")},
                "database_access": "temporary_copy_of_frozen_collection_no_source_write",
                "claim": "raw_retrieval_ranks_only_not_answer_or_decision"}
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "out_dir": str(outdir), "questions": 21, "outbound_attempts": 0,
            "manifest_sha256": sha256(outdir/"manifest.json")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=os.environ.get("CAMPUS_RAG_ROOT", "./local-data"))
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--validate", action="store_true")
    choices.add_argument("--self-test", action="store_true")
    choices.add_argument("--run", action="store_true")
    parser.add_argument("--allow-benchmark", action="store_true")
    parser.add_argument("--out-dir")
    args = parser.parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    install_outbound_guard()
    try:
        if args.self_test:
            result = self_test()
        elif args.validate:
            ctx = preflight(pathlib.Path(args.project_root))
            result = {"ok": True, "identity": ctx["identity"], "candidates": len(ctx["candidates"]),
                      "questions": len(ctx["gold"]["questions"]), "blocked_outbound": OUTBOUND_ATTEMPTS}
        else:
            require(args.allow_benchmark and bool(args.out_dir), "benchmark_gate_required")
            outdir = pathlib.Path(args.out_dir)
            require(not outdir.exists() and outdir.parent.is_dir(), "new_out_dir_required")
            ctx = preflight(pathlib.Path(args.project_root))
            result = benchmark(ctx, pathlib.Path(args.project_root), outdir)
        require(not OUTBOUND_ATTEMPTS, "outbound_attempted")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, CheckError) else type(exc).__name__
        print(json.dumps({"ok": False, "error": code, "detail": str(exc),
                          "outbound_attempts": OUTBOUND_ATTEMPTS}, ensure_ascii=False))
        return 4 if OUTBOUND_ATTEMPTS else 3


if __name__ == "__main__":
    raise SystemExit(main())
