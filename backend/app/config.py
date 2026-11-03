from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./octolife.db"
    llm_provider: str = "mock"  # mock | ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_timeout: float = 120  # seconds; small models on CPU are slow
    ollama_num_gpu: int | None = None  # 0 forces CPU-only; unset lets Ollama decide
    user_email: str = "student@localhost"
    user_name: str = "Student"
    user_timezone: str = "UTC"
    job_feed_paths: list[str] = []
    scheduler_enabled: bool = True


settings = Settings()
