from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379"
    # Increase default job timeout to support long video transcriptions
    # (e.g. large lectures may take a long time to transcribe locally).
    # Set via env var `JOB_TIMEOUT` if needed.
    job_timeout: int = 7200         # seconds before a queued job is considered dead (2 hours)
    result_ttl: int = 3600          # seconds to keep finished results in Redis


settings = Settings()
