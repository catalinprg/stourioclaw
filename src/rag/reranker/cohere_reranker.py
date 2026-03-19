from __future__ import annotations
import asyncio
import cohere
from src.rag.reranker.base import BaseReranker, RankedDocument


class CohereReranker(BaseReranker):
    def __init__(self, api_key: str, model: str = "rerank-v3.5"):
        self.model = model
        self._client = cohere.Client(api_key=api_key)

    async def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[RankedDocument]:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.rerank(
                model=self.model,
                query=query,
                documents=documents,
                top_n=top_k,
            ),
        )
        results = []
        for hit in response.results:
            results.append(
                RankedDocument(
                    content=documents[hit.index],
                    score=hit.relevance_score,
                    index=hit.index,
                )
            )
        return results
