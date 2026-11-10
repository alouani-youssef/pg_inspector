from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    RELOAD: bool = False

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

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL for the async asyncpg driver (password is escaped, never logged)."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DATABASE_USERNAME,
            password=self.DATABASE_PASSWORD.get_secret_value() or None,
            host=self.DATABASE_HOST,
            port=self.DATABASE_PORT,
            database=self.DATABASE_DB,
        )


settings = Settings()
