from abc import ABC, abstractmethod


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict
    execution_mode: str = "local"  # "local" | "gateway" | "sandboxed"

    @abstractmethod
    async def execute(self, arguments: dict) -> dict:
        ...

    async def validate(self, arguments: dict) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    def to_tool_definition(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}
