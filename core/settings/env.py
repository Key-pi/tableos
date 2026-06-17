from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    debug: bool = Field(default=False, validation_alias="DJANGO_DEBUG")
    secret_key: str = Field(default="unsafe-dev-secret-key", validation_alias="DJANGO_SECRET_KEY")
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost"],
        validation_alias="DJANGO_ALLOWED_HOSTS",
    )
    csrf_trusted_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="DJANGO_CSRF_TRUSTED_ORIGINS",
    )
    timezone: str = Field(default="Europe/Kiev", validation_alias="DJANGO_TIMEZONE")
    language_code: str = Field(default="ru-ru", validation_alias="DJANGO_LANGUAGE_CODE")
    use_sqlite: bool = Field(default=False, validation_alias="DJANGO_USE_SQLITE")
    sqlite_path: str = Field(default="db.sqlite3", validation_alias="DJANGO_SQLITE_PATH")

    postgres_db: str = Field(default="tableos", validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="tableos", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="tableos", validation_alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")

    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    bot_token_encryption_key: str = Field(default="", validation_alias="BOT_TOKEN_ENCRYPTION_KEY")
    celery_broker_url: str = Field(default="", validation_alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="", validation_alias="CELERY_RESULT_BACKEND")
    celery_task_always_eager: bool = Field(
        default=False,
        validation_alias="CELERY_TASK_ALWAYS_EAGER",
    )
    celery_task_eager_propagates: bool = Field(
        default=True,
        validation_alias="CELERY_TASK_EAGER_PROPAGATES",
    )
    celery_task_ignore_result: bool = Field(
        default=True,
        validation_alias="CELERY_TASK_IGNORE_RESULT",
    )
    notification_broadcast_batch_size: int = Field(
        default=250,
        validation_alias="NOTIFICATIONS_BROADCAST_BATCH_SIZE",
    )

    @field_validator("allowed_hosts", "csrf_trusted_origins", mode="before")
    @classmethod
    def split_csv(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @property
    def database_config(self) -> dict[str, Any]:
        if self.use_sqlite:
            return {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": self.sqlite_path,
            }
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": self.postgres_db,
            "USER": self.postgres_user,
            "PASSWORD": self.postgres_password,
            "HOST": self.postgres_host,
            "PORT": self.postgres_port,
        }


app_settings = AppSettings()
