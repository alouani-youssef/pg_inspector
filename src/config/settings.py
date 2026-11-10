from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

Transport = Literal["streamable-http", "stdio"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    TRANSPORT: Transport = Field(
        default="streamable-http",
        description=(
            "streamable-http serves /mcp over the network (HOST/PORT, API key required); "
            "stdio talks JSON-RPC over stdin/stdout to the client that launched the process."
        ),
    )
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    RELOAD: bool = False

    # Auth: clients must send this key to reach /mcp (the HTTP server refuses to start without
    # it). Not used by the stdio transport.
    API_KEY: SecretStr = SecretStr("")
    PUBLIC_URL: str = Field(
        default="",
        description=(
            "Externally reachable base URL (e.g. https://inspector.example.com), used in OAuth "
            "resource metadata. Empty derives it from each request's scheme and Host."
        ),
    )

    # Database connection
    DATABASE_HOST: str = "127.0.0.1"
    DATABASE_PORT: int = Field(default=5432, ge=1, le=65535)
    DATABASE_USERNAME: str = "root"
    DATABASE_PASSWORD: SecretStr = SecretStr("")
    DATABASE_DB: str = "app"

    # Database pool / timeouts
    DATABASE_POOL_SIZE: int = Field(default=5, ge=1)
    DATABASE_MAX_OVERFLOW: int = Field(default=5, ge=0)
    DATABASE_POOL_TIMEOUT: float = Field(
        default=30.0, gt=0, description="Seconds to wait for a pooled connection."
    )
    DATABASE_POOL_RECYCLE: int = Field(
        default=1800,
        description="Recycle connections older than N seconds (-1 disables).",
    )
    DATABASE_CONNECT_TIMEOUT: float = Field(
        default=10.0, gt=0, description="Seconds to establish a connection."
    )
    DATABASE_STATEMENT_TIMEOUT_MS: int = Field(
        default=30_000, ge=0, description="Server-side statement timeout (0 disables)."
    )
    DATABASE_ECHO: bool = False

    # Tools
    QUERY_MAX_ROWS: int = Field(
        default=1000, ge=1, description="Hard cap on rows returned by query_execute."
    )
    QUERY_MAX_LENGTH: int = Field(
        default=100_000, ge=1, description="Longest SQL text the tools accept."
    )

    # Tool filtering (comma-separated lists; empty = no restriction)
    TOOL_GROUPS: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Domain groups to expose (server, catalog, query). Empty exposes all.",
    )
    TOOLS_INCLUDE: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Tools (full names, e.g. query_explain) to expose even when their group is not enabled.",
    )
    TOOLS_EXCLUDE: Annotated[list[str], NoDecode] = Field(
        default_factory=list, description="Tools never exposed."
    )

    @field_validator("TOOL_GROUPS", "TOOLS_INCLUDE", "TOOLS_EXCLUDE", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL for the async asyncpg driver (password is escaped, never logged)."""
        return self.url_for(self.DATABASE_DB)

    def url_for(self, database: str) -> URL:
        """Same server and credentials as `database_url`, pointed at another database."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DATABASE_USERNAME,
            password=self.DATABASE_PASSWORD.get_secret_value() or None,
            host=self.DATABASE_HOST,
            port=self.DATABASE_PORT,
            database=database,
        )


settings = Settings()
