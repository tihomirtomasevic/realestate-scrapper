from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://adcrawler:adcrawler@db:5432/adcrawler"
    api_cors_origins: str = "http://localhost:8080"
    api_page_size_default: int = 50
    api_page_size_max: int = 200
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


settings = Settings()
