"""
LLM fabrikası — model, parametre ve structured output bağlantılarını tek yerde yönetir.

Eski durum: nodes.py içinde inline _llm() fonksiyonu vardı; tek bir ChatOpenAI nesnesi
hem SQL üretiminde hem özetlemede aynı temperature ile çalışıyordu.

Yeni durum: İki ayrı LLM preset'i + SQLOutput structured output şeması.
  - get_sql_llm()     → SQLOutput'a bağlı; LLM JSON dışında çıktı üretemez.
  - get_summary_llm() → düz ChatOpenAI; özetleme için hafif yaratıcılık (temp=0.3).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from src.config import get_settings


class SQLOutput(BaseModel):
    """
    SQL üretim node'larının LLM'den beklediği yapılandırılmış çıktı.

    Eski yaklaşım: LLM serbest metin döndürürdü → regex ile '-- SOURCE: xxx' etiketi
    ve kod fence ayıklanırdı. LLM format kuralına uymayı unutursa
    (ki unuturdu) parse hatası veya yanlış kaynak seçimi olurdu.

    Yeni yaklaşım: OpenAI Structured Outputs — model token seviyesinde kısıtlanır;
    schema dışı çıktı üretmesi teknik olarak imkânsız. Regex'e, temizleme
    fonksiyonlarına ve format örneği vermeye gerek kalmaz.
    """

    source: Literal["duckdb", "postgres"]
    sql: str
    rationale: str


def _base_llm(**overrides: Any) -> ChatOpenAI:
    """config.py'dan parametreleri okuyarak ChatOpenAI nesnesi üretir."""
    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": s.openai_model,
        "max_tokens": s.openai_max_tokens,
        "timeout": s.openai_request_timeout,
        "api_key": s.openai_api_key.get_secret_value(),
    }
    if s.openai_base_url:
        kwargs["base_url"] = s.openai_base_url
    kwargs.update(overrides)
    return ChatOpenAI(**kwargs)


@lru_cache(maxsize=1)
def get_sql_llm() -> Runnable:
    """
    SQL üretimi + hata düzeltme için structured output'a bağlı LLM singleton.

    .with_structured_output(SQLOutput) çağrısı LangChain'e şunu söyler:
      1. OpenAI'a function-calling / JSON schema modunda istek at.
      2. Gelen JSON'u otomatik olarak SQLOutput Pydantic modeline dönüştür.
    Artık response.content string'i parse etmeye gerek yok; doğrudan output.sql,
    output.source, output.rationale alanlarına erişilir.
    """
    s = get_settings()
    llm = _base_llm(temperature=s.openai_sql_temperature)
    return llm.with_structured_output(SQLOutput)


@lru_cache(maxsize=1)
def get_summary_llm() -> ChatOpenAI:
    """
    Özetleme için düz ChatOpenAI singleton.

    Özetleme serbest metin ürettiği için structured output gerekmez;
    ancak SQL'den farklı olarak hafif yaratıcılık (temperature=0.3) istenir.
    """
    s = get_settings()
    return _base_llm(temperature=s.openai_summary_temperature)