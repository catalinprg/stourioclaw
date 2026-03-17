from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # LLM Providers (defaults to OpenAI; override in .env)
    orchestrator_provider: str = "openai"
    orchestrator_model: str = "gpt-4o-mini"
    agent_provider: str = "openai"
    agent_model: str = "gpt-4o-mini"

    # API Keys
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    # Security
    stourio_api_key: Optional[str] = None
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    # Infrastructure passwords (used by docker-compose, declared here so pydantic doesn't reject them)
    postgres_password: str = "changeme"
    redis_password: str = "changeme"

    # DeepSeek
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Google
    google_model: str = "gemini-2.0-flash"

    # Infrastructure (passwords via env vars, never hardcode)
    database_url: str = "postgresql+asyncpg://stourio:changeme@postgres:5432/stourio"
    redis_url: str = "redis://redis:6379/0"

    # Execution Endpoints
    automation_webhook_url: str = "http://n8n:5678/webhook/stourio"
    mcp_server_url: str = ""
    mcp_shared_secret: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Guardrails
    max_agent_depth: int = 4
    approval_ttl_seconds: int = 300
    kill_switch_key: str = "stourio:kill_switch"

    # --- RAG ---
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    reranker_provider: str = "cohere"
    cohere_api_key: str = ""
    runbooks_dir: str = "/app/docs"
    # --- Notifications ---
    notification_config_path: str = "config/notifications.yaml"
    # --- Caching ---
    cache_enabled: bool = True
    cache_orchestrator_ttl: int = 300
    cache_agent_ttl: int = 0
    # --- Cost tracking ---
    cost_alert_daily_threshold: float = 0.0
    cost_alert_channel: str = ""
    # --- Agent memory ---
    agent_memory_ttl_days: int = 90
    agent_memory_recall_count: int = 3
    conversation_history_limit: int = 20
    # --- Plugins ---
    tools_yaml_dir: str = "tools/yaml"
    tools_python_dir: str = "tools/python"
    # --- Chains ---
    chains_config_path: str = "config/chains.yaml"
    # --- Agent concurrency & templates ---
    agent_templates_dir: str = "config/agents"
    agent_concurrency_default: int = 3
    agent_concurrency_config: dict = {}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()