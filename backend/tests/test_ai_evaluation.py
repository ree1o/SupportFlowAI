import json
from pathlib import Path

from app.ai.evaluation import FIXTURE, build_prompt, load_scenarios, rank_chunks, score
from app.ai.schemas import GeneratedContent


def test_copilot_evaluation_fixture_is_separate_and_multilingual():
    documents, dev = load_scenarios("development")
    _, heldout = load_scenarios("heldout")
    assert len(dev) == 12
    assert len(heldout) == 24
    assert {item["language"] for item in heldout} == {"en", "ru", "kk"}
    assert all(item["split"] == "development" for item in dev)
    assert all(item["split"] == "heldout" for item in heldout)
    assert all(item["published"] is True for item in documents)


def test_evaluation_prompt_excludes_labels_and_unpublished_revisions():
    documents, heldout = load_scenarios("heldout")
    case = next(item for item in heldout if item["type"] == "conflicting_version")
    built = build_prompt(case, documents)
    assert "answerable" not in built["prompt"]
    assert "relevant_document_ids" not in built["prompt"]
    assert "Old reset-link procedure" not in built["prompt"]
    assert set(built["ref_map"].values()) == {item["id"] for item in documents}
    fixture = json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
    assert any(
        item["id"] == "password_en_old" and not item["published"] for item in fixture["documents"]
    )


def test_evaluation_scorer_measures_citation_and_abstention_contracts():
    _, cases = load_scenarios("heldout")
    case = next(item for item in cases if item["answerable"])
    result = score(
        GeneratedContent(
            answer="Ask for a fresh recovery link.",
            insufficient_evidence=False,
            cited_source_refs=["S1"],
        ),
        case,
        {"S1": case["relevant_document_ids"][0]},
    )
    assert result["valid_citations"] is True
    assert result["source_hit"] is True
    assert result["source_recall"] == 1.0

    insufficient = next(item for item in cases if not item["answerable"])
    abstain = score(
        GeneratedContent(answer="", insufficient_evidence=True, cited_source_refs=[]),
        insufficient,
        {"S1": "password_en"},
    )
    assert abstain["abstained"] is True
    assert abstain["source_hit"] is False


def test_evaluation_retrieval_uses_bounded_exact_cosine_candidates():
    chunks = [
        {"id": "a", "chunk_index": 0},
        {"id": "b", "chunk_index": 0},
        {"id": "c", "chunk_index": 0},
        {"id": "d", "chunk_index": 0},
        {"id": "e", "chunk_index": 0},
    ]
    vectors = [[0.9, 0.1], [0.8, 0.2], [0.7, 0.3], [0.6, 0.4], [0.2, 0.8]]
    ranked = rank_chunks(chunks, vectors, [1.0, 0.0])
    assert [item["id"] for item in ranked] == ["a", "b", "c", "d"]
    assert all(item["relevance"] >= 0.30 for item in ranked)
