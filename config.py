"""Central configuration for the Patternverse MIS API.

Reads environment variables (optionally from a local ``.env`` file) and exposes
a cached ``Settings`` object. The single most important knob is ``APP_ENV``,
which decides whether the model service talks to Ollama or Hugging Face.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Environment toggle — "development" (Ollama) or "production" (Hugging Face)
    app_env: str = "development"

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # Hugging Face (production)
    hf_api_token: str = ""
    hf_model_url: str = (
        "https://api-inference.huggingface.co/models/"
        "mistralai/Mistral-7B-Instruct-v0.3"
    )

    # Ollama (local dev)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Derived helpers ────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def model_backend(self) -> str:
        """Human-readable name of the active model backend."""
        return "huggingface" if self.is_production else "ollama"

    @property
    def supabase_key(self) -> str:
        """Prefer the service-role key on the server; fall back to anon."""
        return self.supabase_service_key or self.supabase_anon_key

    @property
    def supabase_configured(self) -> bool:
        """True if any Supabase URL + key pair is present."""
        return bool(self.supabase_url and self.supabase_key)

    @property
    def supabase_active(self) -> bool:
        """True only when Supabase can actually serve this trusted backend.

        Row Level Security is enabled on the tables, so the anon key (which has
        no user JWT, hence auth.uid() = null) cannot read or write. The backend
        therefore requires the SERVICE-ROLE key. Until it's present we stay on
        the in-memory store rather than activate a non-functional connection.
        """
        return bool(self.supabase_url and self.supabase_service_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
