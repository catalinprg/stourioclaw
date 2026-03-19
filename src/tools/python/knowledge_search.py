from src.mcp.legacy.base import BaseTool

_retriever = None


def set_retriever(retriever):
    global _retriever
    _retriever = retriever


class KnowledgeSearchTool(BaseTool):
    name = "search_knowledge"
    description = "Search internal documentation, runbooks, and past agent experiences"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language search query"},
            "source_type": {
                "type": "string",
                "enum": ["runbook", "agent_memory", "incident"],
                "description": "Optional: filter by source type",
            },
        },
        "required": ["query"],
    }
    execution_mode = "local"

    async def execute(self, arguments: dict) -> dict:
        if not _retriever:
            return {"error": "RAG retriever not initialized"}
        results = await _retriever.search(
            query=arguments.get("query", ""),
            source_type=arguments.get("source_type"),
        )
        return {
            "results": [
                {
                    "content": r.content,
                    "score": round(r.score, 3),
                    "source": r.source_path,
                    "section": r.section_header,
                }
                for r in results
            ]
        }
