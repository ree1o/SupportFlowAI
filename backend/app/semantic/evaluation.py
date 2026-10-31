"""Held-out semantic/lexical retrieval evaluation using a disposable *_test database."""

import json
import os
import re
import statistics
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.incidents.correlation import significant_terms
from app.semantic.embedding import get_local_embedder

FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "semantic_retrieval_heldout.json"
)
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
K = 5


def metrics(ranked: dict[str, list[str]], relevant: dict[str, str]) -> dict[str, float | int]:
    hits = [int(relevant[qid] in ids[:K]) for qid, ids in ranked.items()]
    reciprocal = [
        1 / (ids.index(relevant[qid]) + 1) if relevant[qid] in ids[:K] else 0.0
        for qid, ids in ranked.items()
    ]
    total = len(relevant)
    hit_count = sum(hits)
    return {
        "queries": total,
        "relevant_documents": total,
        "relevant_in_top_k": hit_count,
        "precision_at_5": round(hit_count / (total * K), 4),
        "recall_at_5": round(hit_count / total, 4),
        "mrr_at_5": round(sum(reciprocal) / total, 4),
    }


def relevant_ranks(ranked: dict[str, list[str]], relevant: dict[str, str]) -> dict[str, int | None]:
    return {
        query_id: (ids.index(relevant[query_id]) + 1 if relevant[query_id] in ids else None)
        for query_id, ids in ranked.items()
    }


def simple_lexical(query: str, candidate: str) -> float:
    left = set(TOKEN.findall(query.casefold()))
    right = set(TOKEN.findall(candidate.casefold()))
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def lexical_v1_adaptation(query: dict, candidate: dict) -> float:
    left = significant_terms(query["text"])
    right = significant_terms(candidate["text"])
    shared = left & right
    if len(shared) < 2:
        return 0.0
    jaccard = len(shared) / len(left | right)
    category_match = query["category"] == candidate["category"]
    temporal = max(0.0, 1 - candidate["minutes_from_query"] / 360)
    return 0.65 * jaccard + 0.20 * category_match + 0.15 * temporal


def run() -> dict:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url or not (make_url(raw_url).database or "").endswith("_test"):
        raise RuntimeError(
            "Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test"
        )
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    queries = fixture["queries"]
    candidates = fixture["candidates"]
    relevant = {query["id"]: query["relevant"] for query in queries}
    settings = Settings()
    embedder = get_local_embedder(settings.semantic_model_cache_dir)

    started = time.perf_counter()
    doc_vectors = {item["id"]: embedder.embed_document(item["text"]) for item in candidates}
    indexing_seconds = time.perf_counter() - started
    query_vectors: dict[str, list[float]] = {}
    query_embedding_ms: list[float] = []
    for query in queries:
        one = time.perf_counter()
        query_vectors[query["id"]] = embedder.embed_document(query["text"])
        query_embedding_ms.append((time.perf_counter() - one) * 1000)

    simple_ranked: dict[str, list[str]] = {}
    adapted_ranked: dict[str, list[str]] = {}
    simple_times: list[float] = []
    adapted_times: list[float] = []
    for query in queries:
        started = time.perf_counter()
        simple_ranked[query["id"]] = [
            item["id"]
            for item in sorted(
                candidates,
                key=lambda item: (-simple_lexical(query["text"], item["text"]), item["id"]),
            )
        ]
        simple_times.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        adapted_ranked[query["id"]] = [
            item["id"]
            for item in sorted(
                candidates,
                key=lambda item: (
                    -lexical_v1_adaptation(query, item),
                    item["id"],
                ),
            )
        ]
        adapted_times.append((time.perf_counter() - started) * 1000)

    url = make_url(raw_url)
    engine = create_engine(url, hide_parameters=True)
    schema = f"semantic_eval_{uuid4().hex}"
    table = f'"{schema}"."semantic_vectors"'
    semantic_ranked: dict[str, list[str]] = {}
    pg_query_times: list[float] = []
    relation_bytes = 0
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(
                    f"CREATE TABLE {table} (tenant_id text NOT NULL, ticket_id text NOT NULL, "
                    "embedding public.vector(384) NOT NULL, PRIMARY KEY (tenant_id, ticket_id))"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {table} (tenant_id, ticket_id, embedding) "
                    "VALUES (:tenant, :id, CAST(:vector AS public.vector(384)))"
                ),
                [
                    {
                        "tenant": "fixture-org",
                        "id": item["id"],
                        "vector": str(doc_vectors[item["id"]]),
                    }
                    for item in candidates
                ]
                + [
                    {
                        "tenant": "other-org",
                        "id": "foreign-nearest",
                        "vector": str(query_vectors[queries[0]["id"]]),
                    }
                ],
            )
            for query in queries:
                started = time.perf_counter()
                ids = (
                    connection.execute(
                        text(
                            f"SELECT ticket_id FROM {table} WHERE tenant_id = :tenant "
                            "ORDER BY embedding <=> CAST(:vector AS public.vector(384)), "
                            "ticket_id LIMIT :limit"
                        ),
                        {
                            "tenant": "fixture-org",
                            "vector": str(query_vectors[query["id"]]),
                            "limit": K,
                        },
                    )
                    .scalars()
                    .all()
                )
                pg_query_times.append((time.perf_counter() - started) * 1000)
                semantic_ranked[query["id"]] = list(ids)
            relation_bytes = (
                connection.scalar(
                    text("SELECT pg_total_relation_size(:table)"),
                    {"table": f"{schema}.semantic_vectors"},
                )
                or 0
            )

        return {
            "dataset": fixture["dataset"],
            "split": fixture["split"],
            "queries": len(queries),
            "candidate_count": len(candidates),
            "candidate_corpus_per_query": len(candidates),
            "k": K,
            "methods": {
                "simple_lexical": {
                    **metrics(simple_ranked, relevant),
                    "mean_ranking_ms": round(statistics.mean(simple_times), 3),
                },
                "lexical_v1_adaptation": {
                    **metrics(adapted_ranked, relevant),
                    "mean_ranking_ms": round(statistics.mean(adapted_times), 3),
                },
                "semantic_pgvector_exact": {
                    **metrics(semantic_ranked, relevant),
                    "mean_query_embedding_ms": round(statistics.mean(query_embedding_ms), 2),
                    "mean_pg_query_ms": round(statistics.mean(pg_query_times), 3),
                },
            },
            "relevant_rank_by_query": {
                "simple_lexical": relevant_ranks(simple_ranked, relevant),
                "lexical_v1_adaptation": relevant_ranks(adapted_ranked, relevant),
                "semantic_pgvector_exact": relevant_ranks(semantic_ranked, relevant),
            },
            "embedding_indexing": {
                "documents": len(candidates),
                "elapsed_seconds": round(indexing_seconds, 3),
                "documents_per_second": round(len(candidates) / indexing_seconds, 2),
                "raw_vector_bytes": len(candidates) * 384 * 4,
                "postgres_table_bytes_with_heap_and_pk": relation_bytes,
            },
            "tenant_filter_check": {
                "foreign_candidate_inserted": True,
                "returned_to_fixture_org": all(
                    item != "foreign-nearest"
                    for values in semantic_ranked.values()
                    for item in values
                ),
            },
        }
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
