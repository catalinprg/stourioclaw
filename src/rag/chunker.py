from __future__ import annotations
import re
import hashlib
from typing import Optional


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        import yaml
        raw = text[3:end].strip()
        metadata = yaml.safe_load(raw) or {}
    except Exception:
        metadata = {}
    body = text[end + 4:].lstrip("\n")
    return metadata, body


def _split_by_headers(text: str) -> list[tuple[str, str]]:
    """Split markdown by h1/h2/h3 headers. Returns list of (header, content)."""
    pattern = re.compile(r"^(#{1,3} .+)$", re.MULTILINE)
    positions = [(m.start(), m.group(0)) for m in pattern.finditer(text)]

    if not positions:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    # Content before first header
    if positions[0][0] > 0:
        preamble = text[: positions[0][0]].strip()
        if preamble:
            sections.append(("", preamble))

    for i, (pos, header) in enumerate(positions):
        start = pos + len(header)
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[start:end].strip()
        sections.append((header.lstrip("#").strip(), content))

    return sections


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _split_by_paragraphs(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split text into overlapping chunks by paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        if current_tokens + para_tokens > max_tokens and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Carry over overlap
            overlap_parts: list[str] = []
            overlap_count = 0
            for part in reversed(current_parts):
                part_tokens = _estimate_tokens(part)
                if overlap_count + part_tokens > overlap_tokens:
                    break
                overlap_parts.insert(0, part)
                overlap_count += part_tokens
            current_parts = overlap_parts
            current_tokens = overlap_count

        current_parts.append(para)
        current_tokens += para_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks or [text]


def chunk_markdown(
    text: str,
    source_path: str,
    max_tokens: int = 512,
    overlap_tokens: int = 50,
) -> list[dict]:
    """Parse and chunk a markdown document into overlapping sections."""
    metadata, body = _parse_frontmatter(text)
    sections = _split_by_headers(body)

    chunks: list[dict] = []
    for header, content in sections:
        if not content:
            continue

        if _estimate_tokens(content) <= max_tokens:
            sub_chunks = [content]
        else:
            sub_chunks = _split_by_paragraphs(content, max_tokens, overlap_tokens)

        for sub in sub_chunks:
            sub = sub.strip()
            if not sub:
                continue
            content_hash = hashlib.sha256(sub.encode()).hexdigest()
            chunk_meta = dict(metadata)
            chunk_meta["content_hash"] = content_hash
            chunks.append(
                {
                    "section_header": header,
                    "content": sub,
                    "source_path": source_path,
                    "metadata": chunk_meta,
                }
            )

    return chunks
