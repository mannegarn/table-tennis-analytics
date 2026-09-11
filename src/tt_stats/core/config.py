"""Application settings loaded from the environment and a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the pipeline.

    Field names map to upper-case environment variables automatically, so
    ``postgres_host`` reads ``POSTGRES_HOST``. Values come from the process
    environment first, then from ``.env``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Postgres connection parts.
    postgres_user: str = "tt"
    # Required: no default credential is shipped in the repository.
    postgres_password: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "tt_stats"

    # Optional full DSN. When set it wins over the individual parts above,
    # which is handy for deployed environments that inject a single URL.
    database_url: str | None = None

    # Ingestion knobs shared by the collectors.
    max_concurrent_requests: int = 50
    request_timeout_seconds: float = 40.0
    chunk_size: int = 200

    @property
    def dsn(self) -> str:
        """Return the PostgreSQL connection string."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so repeated calls do not re-read the environment, and so tests can
    clear the cache when they patch the environment.
    """
    return Settings()
