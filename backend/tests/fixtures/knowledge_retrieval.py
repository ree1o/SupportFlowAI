"""Synthetic retrieval fixture accessors; relevance labels never enter embedder inputs."""

import json
from pathlib import Path
from typing import TypedDict


class Topic(TypedDict):
    id: str
    category: str
    documents: dict[str, tuple[str, str]]
    queries: list[tuple[str, str]]


FIXTURE_PATH = Path(__file__).with_name("knowledge_retrieval.json")
TOPICS: list[Topic] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def corpus() -> list[dict[str, str]]:
    return [
        {
            "id": f"{topic['id']}_{language}",
            "topic": topic["id"],
            "category": topic["category"],
            "language": language,
            "text": f"{title}\n{body}",
        }
        for topic in TOPICS
        for language, (title, body) in topic["documents"].items()
    ]


def queries() -> list[dict[str, str | list[str]]]:
    result = []
    for topic in TOPICS:
        for index, (language, text) in enumerate(topic["queries"]):
            result.append(
                {
                    "id": f"{topic['id']}-{index + 1}",
                    "topic": topic["id"],
                    "language": language,
                    "text": text,
                    "relevant": [f"{topic['id']}_{code}" for code in ("en", "ru", "kk")],
                    "split": "development" if index == 0 else "heldout",
                }
            )
    return result
