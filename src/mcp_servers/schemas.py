##--MCP tool'unun ağzından çıkan her JSON'un şeklini garanti eder — böylece agent, prompt, testler ve tool'lar aynı sözleşmeye göre yazılır. --##
"""
Neden bu kadar merkezi?
  - Tüm tool'lar aynı zarf döner. Bir yerde alan adı değişirse (row_count → rows) her yeri kırar. Tek yerde tanımlı olması refactor'ü sadeleştirir.
  - Correct-by-construction: QueryResult(status="success", ...) yazamazsın çünkü Literal "success" | "error" dışına çıkamazsın. Typo "succes" → pydantic başlangıçta hata verir.
  - Self-correction loop'un yakıtı: Hata yanıtı {"status":"error","error_type":"...","message":"..."} yapılandırılmış olduğu için LangGraph node'u if result.status == "error": route_to_error_analysis() diyebilir.
   Metin parse etmez.
  - Fabrika metodları (.ok(), .fail()) çağırma noktasında olası tutarsızlıkları önler — başarılı yanıt üretirken yanlışlıkla error_type dolduramazsın.
"""

from __future__ import annotations

from typing import Any, Literal

"""ConfigDict -> Pydantic modellerinin davranışlarını (katılık derecesi, extra alanlar, mutable veri tipleri) yapılandırmak için kullanılır"""
from pydantic import BaseModel, ConfigDict, Field


class ColumnInfo(BaseModel):
    """describe_table tool'unun 'data' listesindeki tek eleman."""

    name: str
    dtype: str
    nullable: bool = True


class QueryResult(BaseModel):
    """
    Tüm MCP tool'larının ortak yanıt zarfı.

      Başarılı: status='success', data doldurulur.
      Hatalı:   status='error', error_type + message doldurulur.
    """
    # Şemada tanımlanmamış fazladan (extra) hiçbir alanın (property) nesneye eklenmesine izin vermez.
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error"] #Literal -> Alabileceği Değer kümsenini sınırlandırı
    row_count: int = 0

    """Python'da varsayılan değer olarak boş liste [] yazmak bellekte aynı nesnenin paylaşılmasına (mutable default argument bug) yol açabilir.
    default_factory=list sayesinde her yeni QueryResult nesnesi oluşturulduğunda sıfır, bağımsız yeni bir liste örneği üretilir."""
    columns: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)

    elapsed_ms: int = 0
    error_type: str | None = None
    message: str | None = None

    """row_count değerini manuel girmeyi engeller, otomatik olarak len(data) üzerinden hesaplar. Başarılı bir yanıtta status="success" atamasını otomatik yapa"""
    @classmethod
    def ok(cls, *, data: list[dict[str, Any]], columns: list[str], elapsed_ms: int) -> QueryResult:
        return cls(
            status="success", row_count=len(data), columns=columns, data=data, elapsed_ms=elapsed_ms
        )

    @classmethod
    def fail(cls, *, error_type: str, message: str, elapsed_ms: int = 0) -> QueryResult:
        return cls(status="error", error_type=error_type, message=message, elapsed_ms=elapsed_ms)
