"""Explicit model-cache setup command; never called by migrations or app startup."""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

from app.knowledge.embedding import MODEL_FILES, KnowledgeEmbedder
from app.knowledge.models import KB_MODEL_ID, KB_MODEL_REPOSITORY, KB_MODEL_REVISION


def main() -> None:
    cache_dir = Path(
        os.environ.get(
            "SUPPORTFLOW_SEMANTIC_MODEL_CACHE_DIR",
            ".cache/supportflow/models",
        )
    ).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=KB_MODEL_REPOSITORY,
        revision=KB_MODEL_REVISION,
        cache_dir=str(cache_dir),
        allow_patterns=MODEL_FILES,
    )
    embedder = KnowledgeEmbedder(str(cache_dir))
    if len(embedder.embed_query("How do I reset my password?")) != 1024:
        raise RuntimeError("Pinned Knowledge Base model returned an unexpected vector size")
    print(f"Installed {KB_MODEL_ID} at immutable revision {KB_MODEL_REVISION}")


if __name__ == "__main__":
    main()
