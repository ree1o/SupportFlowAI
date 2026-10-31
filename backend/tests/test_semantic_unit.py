import math

import pytest

from app.semantic.embedding import (
    FastEmbedAdapter,
    content_digest,
    embedding_text,
    validate_vector,
)
from app.semantic.models import MODEL_ID, MODEL_REPOSITORY_REVISION, VECTOR_DIMENSIONS


def test_embedding_input_redacts_common_personal_and_credential_patterns():
    value = embedding_text(
        "Card declined for person@example.com",
        "Bearer abc123 password=hunter2 call +1 (555) 234-5678 at https://example.test/?token=secret",
    )
    assert "person@example.com" not in value
    assert "abc123" not in value
    assert "hunter2" not in value
    assert "555" not in value
    assert "example.test" not in value


def test_content_digest_is_stable_and_does_not_depend_on_personal_values():
    assert content_digest("same issue", "Email user1@example.test") == content_digest(
        "same issue", "Email user2@example.test"
    )
    assert content_digest("same issue", "first public body") != content_digest(
        "same issue", "different public body"
    )


def test_model_identifier_and_vector_shape_are_explicit():
    assert MODEL_ID == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert MODEL_REPOSITORY_REVISION == "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
    vector = [1.0] + [0.0] * (VECTOR_DIMENSIONS - 1)
    assert validate_vector(vector) == vector
    normalized = validate_vector([2.0, 2.0] + [0.0] * (VECTOR_DIMENSIONS - 2))
    assert math.isclose(sum(value * value for value in normalized), 1.0)
    with pytest.raises(ValueError):
        validate_vector([1.0])
    with pytest.raises(ValueError):
        validate_vector([float("nan")] + [0.0] * (VECTOR_DIMENSIONS - 1))


def test_model_adapter_fails_without_downloading_an_uninstalled_artifact(tmp_path):
    with pytest.raises(FileNotFoundError, match="Pinned local semantic model is not installed"):
        FastEmbedAdapter(str(tmp_path))
