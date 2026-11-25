from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.knowledge.embedding import QUERY_INSTRUCTION, KnowledgeEmbedder
from app.knowledge.models import KB_MODEL_REPOSITORY, KB_MODEL_REVISION


class FakeTextEmbedding:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.query_inputs: list[str] = []

    def embed(self, texts):
        return iter([np.array([3.0] + [0.0] * 1023) for _ in texts])

    def query_embed(self, texts):
        self.query_inputs.extend(texts)
        return iter([np.array([0.0, 4.0] + [0.0] * 1022) for _ in texts])


def test_adapter_requires_preinstalled_pinned_snapshot(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="not installed"):
        KnowledgeEmbedder(str(tmp_path))


def test_adapter_is_local_normalizes_vectors_and_prefixes_only_queries(tmp_path: Path):
    model_path = (
        tmp_path
        / f"models--{KB_MODEL_REPOSITORY.replace('/', '--')}"
        / "snapshots"
        / KB_MODEL_REVISION
    )
    (model_path / "onnx").mkdir(parents=True)
    (model_path / "onnx" / "model_quantized.onnx").touch()
    fake = FakeTextEmbedding()
    with patch("fastembed.TextEmbedding", return_value=fake) as constructor:
        adapter = KnowledgeEmbedder(str(tmp_path))
    assert constructor.call_args.kwargs["local_files_only"] is True
    assert constructor.call_args.kwargs["specific_model_path"] == str(model_path)
    document = adapter.embed_documents(["public support guidance"])[0]
    query = adapter.embed_query("How can I reset the password?")
    assert document[0] == pytest.approx(1.0)
    assert query[1] == pytest.approx(1.0)
    assert fake.query_inputs == [
        f"Instruct: {QUERY_INSTRUCTION}\nQuery:How can I reset the password?"
    ]


@pytest.mark.parametrize(
    "values",
    [
        [0.0] * 1024,
        [float("nan")] + [0.0] * 1023,
        [1.0, 2.0],
    ],
)
def test_invalid_embedding_vectors_are_rejected(values):
    with pytest.raises(ValueError, match="invalid vector|zero vector"):
        KnowledgeEmbedder._validate(values)
