##--Projede oluşabilecek her farklı türden hatayı kendine ait bir sınıfla temsil eder, böylece agent hatayı yakalayan yerde "bu ne tür bir hataydı?" diye tahmin yürütmez--##
"""
Self-correction loop'un yakıtı budur: LangGraph node'u SQL çalışırken hata alırsa, hatanın türüne göre farklı dallanacak:
    - SQLExecutionError → LLM'e "bu SQL'i düzelt" prompt'u
    - GuardrailViolation → LLM'e "yasak sorgu ürettin, farklı bir yol dene"
    - MCPConnectionError → retry değil, kullanıcıya bildir (altyapı problemi)
    - MaxRetriesExceeded → temiz sonlanma, sessizce None dönme yasağı
    - Her exception, LLM'e verilecek bilgiyi attribute olarak taşır (query, engine) — string interpolation zamanı kaybolmasın.
"""

from __future__ import annotations

class CopilotError(Exception):
    """Projedeki tüm custom exception'ların ortak atası.
    Standart Python'da except Exception yazarsak KeyboardInterrupt, SystemExit, MemoryError bile yakalanır — bunlar yakalanmamalı.
    except CopilotError yazınca sadece bizim ürettiğimiz hatalar yakalanır; sistemsel
    hatalar yukarı çıkar. Bu, "gizli bug" avlamayı kolaylaştırır.
    """

class GuardrailViolation(CopilotError):
    """SQL guardrail'ının reddettiği sorgu.
    Örnek durumlar: DDL/DML denemesi, DATA_DIR dışı dosya erişimi,
    LIMIT üstünde sonuç, timeout aşımı.
    """
    def __init__(self, reason: str, *, query: str | None = None) -> None:
        self.reason = reason
        self.query = query
        super().__init__(f"GuardrailViolation: {reason}")

class SQLExecutionError(CopilotError):
    """
    DuckDB veya PostgreSQL'in fırlattığı sorgu çalıştırma hatası.
    Self-correction loop bu mesajı LLM'e AYNEN verir.
    LLM, tam hata metni + tam SQL ile SQL'i düzeltmeyi dener.
    """
    def __init__(self, message: str, *, query: str, engine: str) -> None:
        self.query = query
        self.engine = engine
        super().__init__(f"[{engine}] {message}")

class MCPConnectionError(CopilotError):
    """
    MCP stdio süreci başlatılamadı, bağlantı düştü veya handshake başarısız. Bu hata retry edilmez — altyapı problemidir, kullanıcıya bildirilir.
    """

class MaxRetriesExceeded(CopilotError):
    """
    LangGraph self-correction loop MAX_RETRIES sonrası düzeltemedi. 'graph, kullanıcıya açıklayıcı bir başarısızlık mesajıyla
    temiz şekilde sonlanır — sessizce None dönmez.'
    """
    def __init__(self, attempts: int, last_error: str) -> None:
        self.attempts = attempts
        self.last_error = last_error
        #Mesajı Zenginleştirme
        super().__init__(
            f"Failed after {attempts} attempts. Last error: {last_error}"
        )

