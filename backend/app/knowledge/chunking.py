"""Deterministic Markdown-friendly text chunking with bounded overlap."""

import re

MAX_CHUNK_CHARS = 2000
OVERLAP_CHARS = 240


def chunk_text(content: str) -> list[str]:
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > MAX_CHUNK_CHARS:
            piece, paragraph = (
                paragraph[:MAX_CHUNK_CHARS],
                paragraph[MAX_CHUNK_CHARS - OVERLAP_CHARS :],
            )
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece.strip())
        if current and len(current) + len(paragraph) + 1 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = current[-OVERLAP_CHARS:] + " " + paragraph
        else:
            current = f"{current} {paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks
