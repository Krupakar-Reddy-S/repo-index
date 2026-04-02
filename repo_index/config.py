"""Configuration for repo-index."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment variables and .env file."""

    # Data directory for DB, clones, etc. Default: ./data relative to cwd
    repoindex_data_dir: str = ""
    # GitHub personal access token (optional, increases rate limit from 60 to 5000/hr)
    github_token: str = ""
    # OpenAI API key (for embeddings, Phase 7)
    openai_api_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def data_dir(self) -> Path:
        if self.repoindex_data_dir:
            p = Path(self.repoindex_data_dir)
        else:
            p = Path.cwd() / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "repo-index.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def clones_dir(self) -> Path:
        d = self.data_dir / "clones"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def sources_file(self) -> Path:
        return Path.cwd() / "sources.toml"


settings = Settings()
