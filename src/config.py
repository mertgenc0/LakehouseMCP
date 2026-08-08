##--- Projedeki her modülün ortam değişkenlerine (API Key, DB URL) eriştiği tek nokta ---##
"""
Global configuration — single source of truth.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Tüm runtime konfigürasyonu; .env dosyasından veya ortam değişkenlerinden okunur."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # bütün capsler eşlenir
        extra="ignore",
    )

    # OpenAI
    openai_api_key: SecretStr  # SecretStr -> Hassas değişkenler için sunulmuştur --- print(openai_api_key) ->  sk-***********
    openai_model: str = "gpt-4o"
    openai_sql_temperature: float = 0.0      # SQL üretimi: deterministik
    openai_summary_temperature: float = 0.3  # Özetleme: hafif yaratıcılık
    openai_max_tokens: int = 4096
    openai_request_timeout: int = 60
    openai_base_url: str | None = None

    # PostgreSQL
    postgres_url: SecretStr
    postgres_schema: str = "public"
    postgres_pool_size: int = 5
    postgres_statement_timeout_ms: int = 30_000

    # Data / DuckDB
    data_dir: Path = Path("./data")
    duckdb_path: str = ":memory:"
    duckdb_threads: int = 4
    duckdb_memory_limt: str = "4GB"

    # MCP Servers
    # Annotated -> tip metadatalarını bir araya getirir, tipe ek bilgi veya özel kurallar bağlamamızı sağlayan bir zarftır.
    # NoDecode -> pydantic-settings'e karmaşık bir JSON decode işlemi yapmamasını, raw string kalmasını söyler.
    mcp_duckdb_server_cmd: str = "python"
    # Eğer default=["a","b"] yazsaydın tüm Settings örnekleri aynı liste referansını paylaşırdı — biri değiştirse hepsi değişirdi. default_factory her seferinde yeni liste üretir

    mcp_duckdb_server_args: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["-m", "src.mcp_servers.duckdb_server"] #Python'daki Mutable Default Argument hatasını önler. Doğrudan default=["-m", ...] yazılsaydı tüm Settings örnekleri bellekte aynı liste referansını (pointer) paylaşırdı. default_factory her nesne üretildiğinde yeni bir liste instance'ı açar.
    )

    mcp_postgres_server_cmd: str = "python"
    mcp_postgres_server_args: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "-m",
            "src.mcp_servers.postgres_server",
        ]  # NoDecoda ->JSON parse denemesi yok.
    )
    mcp_tool_timeout: int = 60
    mcp_transport: Literal["stdio", "http"] = "stdio"

    # HTTP transport (MCP_TRANSPORT=http iken kullanılır)
    mcp_duckdb_http_url: str = "http://127.0.0.1:8001/mcp"
    mcp_duckdb_http_host: str = "127.0.0.1"
    mcp_duckdb_http_port: int = 8001
    mcp_postgres_http_url: str = "http://127.0.0.1:8002/mcp"
    mcp_postgres_http_host: str = "127.0.0.1"
    mcp_postgres_http_port: int = 8002

    # Agent / Guardrials
    max_retries: int = 3
    max_rows_returned: int = 1000
    allow_write_queries: bool = False
    query_timeout: int = 30

    # Observability
    """env'de LOG_LEVEL=YARIM yazarsan pydantic başlangıçta hata fırlatır: "sadece bu 4 değerden biri olabilir". Runtime'da değil, başlangıçta yakalanır"""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO" #Literal -> Değişkenin Alabileceği değerleri verilenlere göre ksıtlar
    log_file: Path = Path("./logs/copilot.log")


    # field_validator: Pydantic kütüphanesine ait bir dekoratördür. Sınıftaki bir değişkene veri atanırken araya girer, veriyi yakalar, kontrol eder veya değiştirir, ardından onay verirse değişkene atar.
    @field_validator("mcp_duckdb_server_args", "mcp_postgres_server_args", mode="before")
    @classmethod #Sen nesneye (instance) değil, doğrudan Sınıfın kendisine (cls) aitsin.
    def _split_csv(cls, v: str | list[str]) -> list[str]:
        """`.env` içindeki 'a,b,c' string'ini list[str]'e çevirir."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


    @field_validator("data_dir", "log_file")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        """~ genişletmesi + mutlak yol; path traversal'a karşı ilk savunma."""
        return v.expanduser().resolve()  # ./data/processed göreceli yolunu mutlak yola (/Users/mertgenc/.../data/processed) çevirir.



#lru_cache - Python'ın functools modülünden gelir. Fonksiyonun sonucunu hafızaya (belleğe/cache) kaydeder.
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process başına tek Settings örneği döner (idempotent, thread-safe)."""
    return Settings()  # type: ignore[call-arg]
