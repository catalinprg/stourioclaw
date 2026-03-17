import pytest
import hashlib
from src.rag.chunker import chunk_markdown, _split_by_headers
from src.rag.ingestion import _hash_file, _should_reingest


def test_chunk_by_headers():
    text = "# Section One\nContent of section one.\n\n## Section Two\nContent of section two.\n\n### Section Three\nContent of section three."
    chunks = chunk_markdown(text, source_path="test.md")
    headers = [c["section_header"] for c in chunks]
    assert "Section One" in headers
    assert "Section Two" in headers
    assert "Section Three" in headers


def test_chunk_preserves_metadata():
    text = "---\ntitle: My Runbook\nauthor: ops-team\n---\n\n# Introduction\nThis is the intro."
    chunks = chunk_markdown(text, source_path="test.md")
    assert len(chunks) > 0
    meta = chunks[0]["metadata"]
    assert meta.get("title") == "My Runbook"
    assert meta.get("author") == "ops-team"


def test_chunk_splits_long_sections():
    # Create a section with many paragraphs to exceed max_tokens=50
    long_content = "\n\n".join([f"Paragraph {i}: " + ("word " * 20) for i in range(20)])
    text = f"# Big Section\n{long_content}"
    chunks = chunk_markdown(text, source_path="test.md", max_tokens=50, overlap_tokens=10)
    # Should have been split into multiple chunks
    big_section_chunks = [c for c in chunks if c["section_header"] == "Big Section"]
    assert len(big_section_chunks) > 1


def test_file_hash():
    content = "Hello, world!"
    result = _hash_file(content)
    expected = hashlib.sha256(content.encode()).hexdigest()
    assert result == expected
    # Deterministic: same input yields same hash
    assert _hash_file(content) == _hash_file(content)


def test_should_reingest():
    content = "Some runbook content"
    file_hash = _hash_file(content)
    # Unchanged: same hash in metadata
    assert _should_reingest(file_hash, {"file_hash": file_hash}) is False
    # Changed: different hash in metadata
    assert _should_reingest(file_hash, {"file_hash": "old_different_hash"}) is True
    # No prior metadata
    assert _should_reingest(file_hash, {}) is True
