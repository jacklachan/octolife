from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./octolife.db"
    llm_provider: str = "mock"
    user_email: str = "student@localhost"
    user_name: str = "Student"
    user_timezone: str = "UTC"
    job_feed_paths: list[str] = []
    scheduler_enabled: bool = True


settings = Settings()
