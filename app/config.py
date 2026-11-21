"""Configurare din variabile de mediu (aceleași nume ca în .env / .env.example).

Aplicația NU folosește contul admin (POSTGRES_USER): se conectează ca API_DB_USER (api_reader),
un rol read-only care vede doar schema core (vezi sql/init/04_api_role.sql).
"""
import os
from dataclasses import dataclass, field
from functools import lru_cache

from psycopg.conninfo import make_conninfo


@dataclass(frozen=True)
class Settings:
    db_host: str = "localhost"
    db_port: str = "5432"
    db_name: str = ""
    db_user: str = ""
    # Secretele au repr=False: nu apar dacă obiectul ajunge într-un log sau traceback.
    db_password: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    pool_min_size: int = 1
    pool_max_size: int = 5
    pool_timeout: float = 5.0  # secunde de așteptare pentru o conexiune din pool
    # LLM pentru /v1/ask (vezi app/llm.py)
    llm_provider: str = "gemini"
    llm_model: str = ""
    llm_fallback_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    # Modelul de embedding se încarcă la pornire (lifespan); 0 = la primul /v1/ask (util în teste).
    preload_embedder: bool = True

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
            application_name="ecommerce-data-api",
            connect_timeout="5",
            # A doua plasă de siguranță, pe lângă setarea rolului: sesiuni read-only.
            options="-c default_transaction_read_only=on",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings(
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=os.getenv("POSTGRES_PORT", "5432"),
        db_name=os.getenv("POSTGRES_DB", ""),
        db_user=os.getenv("API_DB_USER", ""),
        db_password=os.getenv("API_DB_PASSWORD", ""),
        api_key=os.getenv("API_KEY", ""),
        pool_min_size=int(os.getenv("API_POOL_MIN_SIZE", "1")),
        pool_max_size=int(os.getenv("API_POOL_MAX_SIZE", "5")),
        pool_timeout=float(os.getenv("API_POOL_TIMEOUT", "5")),
        llm_provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        preload_embedder=os.getenv("API_PRELOAD_EMBEDDER", "1").strip() not in ("0", "false", "no"),
    )
