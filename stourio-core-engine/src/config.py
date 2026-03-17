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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()