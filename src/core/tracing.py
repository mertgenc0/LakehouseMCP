"""
LangSmith tracing başlatıcı.

Eski durum: Hiçbir gözlemlenebilirlik yoktu. Agent çalışınca terminalde structlog
satırları görünürdü — hangi node ne kadar sürdü, kaç token harcandı, hangi retry'da
geçildi görmek imkânsızdı.

Yeni durum: init_tracing() process başında bir kez çağrılır. LANGCHAIN_TRACING_V2=true
olduğunda LangChain otomatik olarak her LLM çağrısını, her LangGraph node'unu ve
aralarındaki ilişkiyi LangSmith'e gönderir. Ek kod gerekmez — LangChain'in callback
sistemi bunu arka planda halleder.

LangSmith dashboard'unda her agent çalışması için şunlar görünür:
  - Her node (schema_discovery, sql_generation, ...) ve çalışma süresi
  - Her LLM çağrısında gönderilen prompt + alınan yanıt
  - Token kullanımı (prompt / completion / total)
  - Retry zinciri (attempt 1 → hata → attempt 2 → başarı)
  - Toplam maliyet tahmini

API key yoksa veya LANGCHAIN_TRACING_V2=false ise tracing sessizce devre dışı
kalır — uygulama normal çalışmaya devam eder.
"""

from __future__ import annotations

import os

from src.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__, component="tracing")


def init_tracing() -> bool:
    """
    LangSmith tracing'i başlatır.

    LangChain, LANGCHAIN_TRACING_V2 / LANGCHAIN_API_KEY / LANGCHAIN_PROJECT
    ortam değişkenlerini import sırasında okur. Bu fonksiyon config.py'daki
    değerleri os.environ'a yazarak LangChain'in onları görmesini sağlar.

    Returns:
        True → tracing aktif, False → devre dışı (key yok veya kapalı).
    """
    s = get_settings()

    if not s.langchain_tracing_v2 or s.langchain_api_key is None:
        log.info("tracing_disabled", reason="LANGCHAIN_TRACING_V2=false veya API key yok")
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = s.langchain_api_key.get_secret_value()
    os.environ["LANGCHAIN_PROJECT"] = s.langchain_project
    if s.langchain_endpoint:
        os.environ["LANGCHAIN_ENDPOINT"] = s.langchain_endpoint

    log.info(
        "tracing_enabled",
        project=s.langchain_project,
        endpoint=s.langchain_endpoint or "default (us)",
        provider="langsmith",
    )
    return True