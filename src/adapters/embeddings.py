"""Embeddings adapter — uses OpenAI API directly (not OpenRouter)."""

from __future__ import annotations

from openai import AsyncOpenAI


class OpenAIEmbedder:
    """Generates text embeddings via the OpenAI embeddings API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding vector for a text string."""
        response = await self._client.embeddings.create(
            model=self.model, input=text
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts."""
        response = await self._client.embeddings.create(
            model=self.model, input=texts
        )
        return [item.embedding for item in response.data]
