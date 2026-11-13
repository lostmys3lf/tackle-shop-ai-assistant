"""Application configuration module using environment variables."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """Main application settings loaded from environment variables."""

    # Database settings
    postgres_user: str = os.getenv("POSTGRES_USER", "tackle")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    postgres_db: str = os.getenv("POSTGRES_DB", "tackle_shop")
    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))

    # LLM settings
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")

    # Langfuse observability settings
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_base_url: str = os.getenv(
        "LANGFUSE_BASE_URL", "https://cloud.langfuse.com"
    )

    # Router settings
    router_provider: str = os.getenv("ROUTER_PROVIDER", "llm").lower()
    router_jev_model: str = os.getenv("ROUTER_JEV_MODEL", "typesafe/jev")
    cloudflare_account_id: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cloudflare_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")

    # Security settings
    api_secret: str = os.getenv("API_SECRET", "")

    @property
    def database_url(self) -> str:
        """Construct or retrieve the PostgreSQL database connection URL."""
        explicit_url = os.getenv("DATABASE_URL")
        if explicit_url:
            return explicit_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
