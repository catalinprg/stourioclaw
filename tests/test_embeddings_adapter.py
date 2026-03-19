"""Tests for the OpenAI embeddings adapter."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.embeddings import OpenAIEmbedder


EMBEDDING_DIM = 1536


def _fake_embedding(dim: int = EMBEDDING_DIM) -> list[float]:
    return [0.01 * i for i in range(dim)]


def _mock_embedding_response(vectors: list[list[float]]):
    """Build a mock matching openai's EmbeddingResponse shape."""
    data = []
    for i, vec in enumerate(vectors):
        item = MagicMock()
        item.embedding = vec
        item.index = i
        data.append(item)
    resp = MagicMock()
    resp.data = data
    return resp


@pytest.mark.asyncio
async def test_embedder_returns_vector():
    """Single text → list[float] of correct dimension."""
    vec = _fake_embedding()
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=_mock_embedding_response([vec])
    )

    embedder = OpenAIEmbedder(api_key="test-key")
    embedder._client = mock_client

    result = await embedder.embed("hello world")

    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM
    assert all(isinstance(v, float) for v in result)
    mock_client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input="hello world"
    )


@pytest.mark.asyncio
async def test_embed_batch_returns_multiple_vectors():
    """Multiple texts → list of vectors, one per input."""
    vecs = [_fake_embedding(), _fake_embedding()]
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=_mock_embedding_response(vecs)
    )

    embedder = OpenAIEmbedder(api_key="test-key")
    embedder._client = mock_client

    texts = ["hello", "world"]
    result = await embedder.embed_batch(texts)

    assert len(result) == 2
    assert all(len(v) == EMBEDDING_DIM for v in result)
    mock_client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input=texts
    )


@pytest.mark.asyncio
async def test_embedder_custom_model():
    """Verify custom model name is forwarded to the API call."""
    vec = _fake_embedding()
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=_mock_embedding_response([vec])
    )

    embedder = OpenAIEmbedder(api_key="test-key", model="text-embedding-3-large")
    embedder._client = mock_client

    await embedder.embed("test")

    mock_client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-large", input="test"
    )
