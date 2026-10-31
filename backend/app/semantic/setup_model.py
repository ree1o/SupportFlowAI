"""Explicitly download and warm the pinned local semantic embedding model."""

from app.config import Settings
from app.semantic.embedding import install_model_cache


def main() -> None:
    cache_dir = install_model_cache(Settings())
    print(f"Semantic embedding model is available in {cache_dir}")


if __name__ == "__main__":
    main()
