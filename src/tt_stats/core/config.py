"""Application settings loaded from the environment and a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    """Return the repository root for a source or editable checkout.

    This file lives at ``<root>/src/tt_stats/core/config.py``, so three parents
    up from it is the checkout root. That assumption does not hold for a wheel
    install, which is why every setting derived from this path can also be set
    explicitly through the environment.
    """
    return Path(__file__).resolve().parents[3]


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

    # Integration-test database. Kept separate from the parts above so a test
    # run can never resolve to the scraped application database. Both are
    # overridable through TEST_DATABASE_NAME / TEST_DATABASE_URL.
    test_database_name: str = "tt_stats_test"
    test_database_url: str | None = None

    # Ingestion knobs shared by the collectors. Concurrency stays low so the
    # pipeline remains a polite client rather than a load test.
    max_concurrent_requests: int = 6
    request_timeout_seconds: float = 40.0
    chunk_size: int = 200
    max_retry_attempts: int = 5
    retry_wait_min_seconds: float = 2.0
    retry_wait_max_seconds: float = 16.0
    request_jitter_seconds: float = 0.01

    # WTT API client.
    wtt_api_key: str = "S_WTT_882jjh7basdj91834783mds8j2jsd81"
    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 11.0; Surface Duo) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    )
    verify_ssl: bool = True

    # Local scratch area for captures, logs and notebooks. Defaults to the
    # repository's ``data/`` directory rather than the current working
    # directory, so running a command from elsewhere cannot scatter files.
    data_dir: Path = Field(default_factory=lambda: project_root() / "data")

    # Numbered SQL migrations. Left unset by default so pure discovery does not
    # require a fully populated Settings object; ``migrations.resolve_migrations_dir``
    # falls back to the repository's ``db/migrations``.
    migrations_dir: Path | None = None

    # Logging. Read by configure_logging() at process entry points.
    # ``log_level`` is the file handler level: the audit trail records
    # everything. ``log_console_level`` keeps routine progress off the
    # terminal, so it cannot collide with the progress bar.
    log_level: str = "INFO"
    log_console_level: str = "WARNING"
    log_json: bool = False

    # Durable log file. Console output always happens; the file handler is the
    # record that survives a closed terminal. It defaults to the repository's
    # ``logs/`` directory for the same reason ``data_dir`` does. Rotation is
    # size based so an unattended backfill cannot fill the disk.
    log_to_file: bool = True
    logs_dir: Path = Field(default_factory=lambda: project_root() / "logs")
    log_file_max_bytes: int = 5_000_000
    log_file_backup_count: int = 3

    def _dsn_for(self, database: str) -> str:
        """Build a connection string from the individual connection parts."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def dsn(self) -> str:
        """Return the PostgreSQL connection string for the application database."""
        if self.database_url:
            return self.database_url
        return self._dsn_for(self.postgres_db)

    @property
    def test_dsn(self) -> str:
        """Return the connection string for the isolated integration-test database.

        ``database_url`` is intentionally ignored here: that variable describes
        a deployed application database, and letting it win would silently undo
        the isolation this property exists to provide. Use ``TEST_DATABASE_URL``
        when a single URL must be injected.
        """
        if self.test_database_url:
            return self.test_database_url
        return self._dsn_for(self.test_database_name)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so repeated calls do not re-read the environment, and so tests can
    clear the cache when they patch the environment.

    Fields come from the process environment and ``.env``, which is why no
    arguments are passed. The pydantic mypy plugin understands this; pyright
    does not, hence the targeted ignore below.
    """
    return Settings()  # pyright: ignore[reportCallIssue]
