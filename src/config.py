from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- OpenRouter (single LLM gateway) ---
    openrouter_api_key: str = ""
    openrouter_default_model: str = "anthropic/claude-sonnet-4-20250514"
    openrouter_fallback_models: list[str] = []
    openrouter_fallback_enabled: bool = True
    orchestrator_model: str = "openai/gpt-4o-mini"

    # --- Embeddings (direct OpenAI, not routed) ---
    openai_api_key: Optional[str] = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536

    # --- Vision (for image analysis via OpenRouter) ---
    vision_model: str = "openai/gpt-4o"

    # --- Security ---
    stourio_api_key: Optional[str] = None
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    security_audit_interval_seconds: int = 60
    security_inline_enabled: bool = True

    # --- Infrastructure passwords (docker-compose) ---
    postgres_password: str = "changeme"
    redis_password: str = "changeme"

    # --- Infrastructure ---
    database_url: str = "postgresql+asyncpg://stourio:changeme@postgres:5432/stourio"
    redis_url: str = "redis://redis:6379/0"

    # --- Execution Endpoints ---
    automation_webhook_url: str = "http://n8n:5678/webhook/stourio"
    mcp_server_url: str = ""
    mcp_shared_secret: str = ""

    # --- Search ---
    search_api_key: str = ""

    # --- Workspace ---
    workspace_dir: str = "/app/workspace"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""
    telegram_use_polling: bool = False
    telegram_allowed_user_ids: list[int] = []

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # --- Guardrails ---
    max_agent_depth: int = 4
    approval_ttl_seconds: int = 300
    kill_switch_key: str = "stourio:kill_switch"

    # --- RAG ---
    embedding_provider: str = "openai"
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

    # --- Agent concurrency & templates ---
    agent_templates_dir: str = "config/agents"
    agent_concurrency_default: int = 3
    agent_concurrency_config: dict = {}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
