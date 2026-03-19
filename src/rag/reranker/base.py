from abc import ABC, abstractmethod
from pydantic import BaseModel


class RankedDocument(BaseModel):
    content: str
    score: float
    index: int
    metadata: dict = {}


class BaseReranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[RankedDocument]:
        ...
