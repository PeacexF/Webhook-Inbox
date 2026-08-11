import os
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class LimitsConfig(BaseModel):
    max_payload_size: int = 10 * 1024 * 1024
    request_timeout: int = 10


class RetentionConfig(BaseModel):
    enabled: bool = True
    default_days: int = 30


class SearchConfig(BaseModel):
    enabled: bool = True
    fuzzy: bool = True


class ReplayConfig(BaseModel):
    enabled: bool = True
    timeout: int = 10
    max_retries: int = 3
    retry_delay_seconds: int = 2
    allow_private_networks: bool = False
    allow_redirects: bool = False
    max_redirects: int = 3
    max_response_size: int = 64 * 1024
    worker_enabled: bool = True
    poll_interval: float = 1.0
    lease_timeout: int = 60


class RateLimitConfig(BaseModel):
    enabled: bool = True
    requests_per_minute: int = 120


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    mongo_uri: str = "mongodb://mongodb:27017"
    mongo_database: str = "webhook_inbox"
    admin_username: str = "admin"
    admin_password: str = ""
    log_level: str = "INFO"

    limits: LimitsConfig = LimitsConfig()
    retention: RetentionConfig = RetentionConfig()
    search: SearchConfig = SearchConfig()
    replay: ReplayConfig = ReplayConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Order is precedence: env beats .env beats YAML beats defaults.
        # CONFIG_FILE is read here, not at class definition, so it stays overridable.
        yaml_source = YamlConfigSettingsSource(
            settings_cls, yaml_file=os.getenv("CONFIG_FILE", "config.yaml")
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


def load_settings(config_file: Path | None = None) -> Settings:
    if config_file is not None:
        os.environ["CONFIG_FILE"] = str(config_file)
    return Settings()
