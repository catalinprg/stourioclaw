from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    dimension: int
    model_name: str

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
