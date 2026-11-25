"""Compare local KB retrieval using only synthetic, labeled fixtures.

Labels are consulted after ranking; only query text and document text reach embedders.
Set SUPPORTFLOW_EVAL_CACHE to the existing local model cache. This script is opt-in.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

from fastembed import TextEmbedding

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests" / "fixtures"))
from knowledge_retrieval import corpus, queries  # noqa: E402

from app.incidents.correlation import significant_terms  # noqa: E402

TOP_K = 5
M8_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
M8_REPO = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
M8_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
KB_MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"
KB_REPO = "Qdrant/Qwen3-Embedding-0.6B-onnx"
KB_REVISION = "af95f2c416ffe9379369ad64f9113e865db6112c"
INSTRUCTION = "Retrieve internal customer-support procedures relevant to the question."
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
GENERIC = set("how can i the a an is my me help please issue problem error request".split())


def model(name: str, repo: str, revision: str, cache: str) -> TextEmbedding:
    path = Path(cache) / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
    if not path.is_dir():
        raise RuntimeError(f"Required pinned local model is missing: {name}")
    return TextEmbedding(
        model_name=name,
        cache_dir=cache,
        specific_model_path=str(path),
        local_files_only=True,
        threads=2,
    )


def vecs(values) -> list[list[float]]:
    return [value.tolist() for value in values]


def tokens(text: str) -> set[str]:
    return {item.casefold() for item in TOKEN.findall(text) if len(item) > 1} - GENERIC


def metrics(ranked: list[list[str]], labels: list[set[str]]) -> dict[str, float | int]:
    recalls, precisions, reciprocals, hit_count = [], [], [], 0
    for result, relevant in zip(ranked, labels, strict=True):
        hits = [index for index, item in enumerate(result[:TOP_K], start=1) if item in relevant]
        hit_count += bool(hits)
        recalls.append(len(hits) / len(relevant))
        precisions.append(len(hits) / TOP_K)
        reciprocals.append(1 / hits[0] if hits else 0)
    return {
        "queries": len(labels),
        "queries_with_hit": int(hit_count),
        "precision_at_5": round(statistics.mean(precisions), 4),
        "recall_at_5": round(statistics.mean(recalls), 4),
        "mrr_at_5": round(statistics.mean(reciprocals), 4),
    }


def run() -> dict:
    m8_cache = os.environ.get("SUPPORTFLOW_M8_CACHE")
    qwen_cache = os.environ.get("SUPPORTFLOW_KB_CACHE")
    if not m8_cache or not qwen_cache:
        raise RuntimeError("Set SUPPORTFLOW_M8_CACHE and SUPPORTFLOW_KB_CACHE to existing caches")
    docs = corpus()
    examples = [query for query in queries() if query["split"] == "heldout"]
    doc_texts = [doc["text"] for doc in docs]
    labels = [set(query["relevant"]) for query in examples]
    doc_tokens = [tokens(text) for text in doc_texts]
    lexical_start = time.perf_counter()
    lexical_rankings = []
    for query in examples:
        query_tokens = tokens(str(query["text"]))
        scores = [
            len(query_tokens & candidate) / max(1, len(query_tokens | candidate))
            for candidate in doc_tokens
        ]
        lexical_rankings.append(
            [
                docs[index]["id"]
                for index in sorted(range(len(docs)), key=lambda i: (-scores[i], docs[i]["id"]))[
                    :TOP_K
                ]
            ]
        )
    lexical_elapsed = time.perf_counter() - lexical_start

    reports: dict[str, object] = {
        "candidate_documents": len(docs),
        "heldout_queries": len(examples),
    }
    reports["lexical_jaccard"] = {
        **metrics(lexical_rankings, labels),
        "total_query_ms": round(lexical_elapsed * 1000, 2),
    }
    m7_rankings = []
    for query in examples:
        query_terms = significant_terms(str(query["text"]))
        scores = []
        for candidate in docs:
            candidate_terms = significant_terms(candidate["text"])
            overlap = len(query_terms & candidate_terms)
            scores.append(2 * overlap / max(1, len(query_terms) + len(candidate_terms)))
        m7_rankings.append(
            [
                docs[index]["id"]
                for index in sorted(range(len(docs)), key=lambda i: (-scores[i], docs[i]["id"]))[
                    :TOP_K
                ]
            ]
        )
    reports["m7_lexical_v1_token_overlap_adaptation"] = metrics(m7_rankings, labels)

    m8 = model(M8_MODEL, M8_REPO, M8_REVISION, m8_cache)
    started = time.perf_counter()
    m8_doc_vecs = vecs(m8.embed(doc_texts))
    m8_index_seconds = time.perf_counter() - started
    m8_rankings, m8_times = [], []
    for query in examples:
        started = time.perf_counter()
        query_vector = vecs(m8.embed([str(query["text"])]))[0]
        m8_times.append((time.perf_counter() - started) * 1000)
        scores = [
            sum(left * right for left, right in zip(query_vector, doc_vector, strict=True))
            for doc_vector in m8_doc_vecs
        ]
        m8_rankings.append(
            [
                docs[index]["id"]
                for index in sorted(range(len(docs)), key=lambda i: (-scores[i], docs[i]["id"]))[
                    :TOP_K
                ]
            ]
        )
    reports["m8_minilm"] = {
        **metrics(m8_rankings, labels),
        "index_seconds_for_36_docs": round(m8_index_seconds, 3),
        "query_ms_mean": round(statistics.mean(m8_times), 2),
        "query_ms_p95": round(sorted(m8_times)[int(0.95 * (len(m8_times) - 1))], 2),
    }

    qwen = model(KB_MODEL, KB_REPO, KB_REVISION, qwen_cache)
    started = time.perf_counter()
    qwen_doc_vecs = vecs(qwen.embed(doc_texts))
    qwen_index_seconds = time.perf_counter() - started
    qwen_rankings, qwen_times = [], []
    for query in examples:
        text = f"Instruct: {INSTRUCTION}\nQuery:{query['text']}"
        started = time.perf_counter()
        query_vector = vecs(qwen.query_embed([text]))[0]
        qwen_times.append((time.perf_counter() - started) * 1000)
        scores = [
            sum(left * right for left, right in zip(query_vector, doc_vector, strict=True))
            for doc_vector in qwen_doc_vecs
        ]
        qwen_rankings.append(
            [
                docs[index]["id"]
                for index in sorted(range(len(docs)), key=lambda i: (-scores[i], docs[i]["id"]))[
                    :TOP_K
                ]
            ]
        )
    reports["qwen3_embedding_q"] = {
        **metrics(qwen_rankings, labels),
        "model_revision": KB_REVISION,
        "index_seconds_for_36_docs": round(qwen_index_seconds, 3),
        "query_ms_mean": round(statistics.mean(qwen_times), 2),
        "query_ms_p95": round(sorted(qwen_times)[int(0.95 * (len(qwen_times) - 1))], 2),
    }
    return reports


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
