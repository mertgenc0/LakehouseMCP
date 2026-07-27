##--LangGraph'ın node'lar arasında taşıdığı tek dict. Soru, şema, SQL, hata, sonuç — hepsi burada. Her node bu dict'in kısmi güncellemesini döner, LangGraph runtime birleştirir.--##

"""
LangGraph agent state.

Node'lar bu TypedDict'in kısmi güncellemesini döner; runtime dict merge yapar.
node'lar saf — State alır, güncellenmiş kısmi State döner.
"""

from __future__ import annotations

from typing import TypedDict
from src.mcp_servers.schemas import QueryResult

class AgentState(TypedDict, total=False):
    """
    Tüm node'lar arasında paylaşılan durum.

    total=False → her node yalnızca değiştirdiği alanları döner;
    LangGraph state'i otomatik merge eder.
    """

    question: str # Kullanıcının doğal dilde sorusu
    schema_context: str  # Formatlanmış tablo/kolon özeti, prompt'a gömülür
    sql: str  # Şu an denenen SQL
    attempt: int  # Kaçıncı deneme (0'dan başlar)
    last_error: str  # DuckDB'nin döndürdüğü ham hata mesajı
    last_error_type: str  # `CatalogException`, `GuardrailViolation`, ...

    # ---------- Başarılı sonuç (mcp_tool_execution) ----------
    result: QueryResult  # QueryResult zarfı

    # ---------- Nihai cevap (summarize node) ----------
    final_answer: str  # Kullanıcıya sunulacak Türkçe özet





