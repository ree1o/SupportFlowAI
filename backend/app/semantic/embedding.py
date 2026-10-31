"""The local embedding adapter; model loading is explicit and never belongs in a migration."""

import hashlib
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.semantic.models import (
    MODEL_ID,
    MODEL_REPOSITORY,
    MODEL_REPOSITORY_REVISION,
    VECTOR_DIMENSIONS,
)

MODEL_FILES = [
    "config.json",
    "model_optimized.onnx",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]

EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", re.IGNORECASE)
URL = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
BEARER = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
SECRET = re.compile(
    r"\b(password|passwd|token|secret|api[_ -]?key|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")


class Embedder(Protocol):
    def embed_document(self, text: str) -> list[float]: ...


def embedding_text(title: str, initial_public_message: str) -> str:
    """Use only title and initial public message, with common PII/credential patterns masked."""
    text = f"{title}\n{initial_public_message}"[:12000]
    for pattern, replacement in (
        (EMAIL, "[email]"),
        (URL, "[url]"),
        (BEARER, "[credential]"),
        (SECRET, r"\1=[credential]"),
        (PHONE, "[phone]"),
    ):
        text = pattern.sub(replacement, text)
    return " ".join(text.split())


def content_digest(title: str, body: str) -> str:
    return hashlib.sha256(embedding_text(title, body).encode("utf-8")).hexdigest()


def validate_vector(values: list[float]) -> list[float]:
    if len(values) != VECTOR_DIMENSIONS or not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding provider returned an invalid vector")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("Embedding provider returned a zero vector")
    return [value / norm for value in values]


class FastEmbedAdapter:
    def __init__(self, cache_dir: str) -> None:
        from fastembed import TextEmbedding

        model_path = (
            Path(cache_dir)
            / f"models--{MODEL_REPOSITORY.replace('/', '--')}"
            / "snapshots"
            / MODEL_REPOSITORY_REVISION
        )
        if not model_path.is_dir() or not (model_path / "model_optimized.onnx").is_file():
            raise FileNotFoundError("Pinned local semantic model is not installed")
        self._model = TextEmbedding(
            model_name=MODEL_ID,
            cache_dir=cache_dir,
            specific_model_path=str(model_path),
            local_files_only=True,
            threads=2,
        )

    def embed_document(self, text: str) -> list[float]:
        vector = next(iter(self._model.embed([text])))
        return validate_vector(vector.tolist())


@lru_cache(maxsize=1)
def get_local_embedder(cache_dir: str) -> FastEmbedAdapter:
    return FastEmbedAdapter(cache_dir)


def install_model_cache(settings: Settings) -> Path:
    """Explicit opt-in setup command that allows the pinned FastEmbed model to download."""
    from huggingface_hub import snapshot_download

    cache_dir = Path(settings.semantic_model_cache_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPOSITORY,
        revision=MODEL_REPOSITORY_REVISION,
        cache_dir=str(cache_dir),
        allow_patterns=MODEL_FILES,
    )
    FastEmbedAdapter(str(cache_dir))
    return cache_dir
