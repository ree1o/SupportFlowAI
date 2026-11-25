"""Pinned, local-only embedding adapter for Knowledge Base content."""

import math
from functools import lru_cache
from pathlib import Path

from app.knowledge.models import KB_MODEL_ID, KB_MODEL_REPOSITORY, KB_MODEL_REVISION

QUERY_INSTRUCTION = "Retrieve internal customer-support procedures relevant to the question."
MODEL_FILES = [
    "config.json",
    "onnx/model_quantized.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
]


class KnowledgeEmbedder:
    def __init__(self, cache_dir: str) -> None:
        from fastembed import TextEmbedding

        model_path = (
            Path(cache_dir)
            / f"models--{KB_MODEL_REPOSITORY.replace('/', '--')}"
            / "snapshots"
            / KB_MODEL_REVISION
        )
        if not model_path.is_dir() or not (model_path / "onnx/model_quantized.onnx").is_file():
            raise FileNotFoundError("Pinned Knowledge Base embedding model is not installed")
        self._model = TextEmbedding(
            model_name=KB_MODEL_ID,
            cache_dir=cache_dir,
            specific_model_path=str(model_path),
            local_files_only=True,
            threads=2,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._validate(vector.tolist()) for vector in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        query = f"Instruct: {QUERY_INSTRUCTION}\nQuery:{text}"
        return self._validate(next(iter(self._model.query_embed([query]))).tolist())

    @staticmethod
    def _validate(values: list[float]) -> list[float]:
        if len(values) != 1024 or not all(math.isfinite(value) for value in values):
            raise ValueError("Knowledge embedding returned an invalid vector")
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("Knowledge embedding returned a zero vector")
        return [value / norm for value in values]


@lru_cache(maxsize=1)
def get_knowledge_embedder(cache_dir: str) -> KnowledgeEmbedder:
    return KnowledgeEmbedder(cache_dir)
