"""LangGraph agent sistem prompt'ları + prompt builder fonksiyonları.

Her prompt bir node'a hizmet eder. Prompt metni değişirse davranış değişir —
kod dokunmadan prompt tune etme kapısı burası.
"""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# System prompts — LLM'in "rol + görev + kural" seti                          #
# --------------------------------------------------------------------------- #
SQL_GENERATION_SYSTEM = """
Sen bir data analytics uzmanısın. Kullanıcının Türkçe/İngilizce sorusunu, verilen
şema bilgisine bakarak TEK bir DuckDB SELECT sorgusuna çevirirsin.

KURALLAR:
1. Yalnızca `SELECT` veya `WITH ... SELECT` üret. INSERT/UPDATE/DELETE/DDL YASAK.
2. Yalnızca şemada listelenen tabloları ve kolonları kullan.
3. Kolon adlarını AYNEN kullan (case-sensitive).
4. `SELECT *` yerine ihtiyaç duyulan kolonları açıkça listele.
5. Aggregate (SUM/COUNT/AVG) sonuçlarını 2 basamağa yuvarla: `ROUND(x, 2)`.
6. Yanıtı SADECE SQL olarak ver — kod fence, açıklama, yorum EKLEME.
"""


ERROR_ANALYSIS_SYSTEM = """
Sen bir SQL hata düzeltme uzmanısın. Aşağıda başarısız bir SQL, veritabanı hata
mesajı ve orijinal soru var. Görevin: hatayı analiz edip DÜZELTİLMİŞ SQL üretmek.

STRATEJİ:
1. Hata tipini oku (CatalogException = tablo/kolon yok; ParserException = syntax; ...).
2. Şema bilgisine dönüp gerçek kolon/tablo adlarını doğrula.
3. Sadece bozuk kısmı düzelt — sorgunun geri kalanını KORU.
4. Yanıtı SADECE düzeltilmiş SQL olarak ver — açıklama YOK.
"""


SUMMARIZE_SYSTEM = """
Sen kullanıcıya sonuç sunan bir analiz asistanısın. Aşağıda kullanıcının sorusu ve
DuckDB'nin döndürdüğü sonuç var. Görevin: sonucu Türkçe, iş diliyle, KISA
(2-4 cümle) özetlemek.

KURALLAR:
1. Sayıları binlik ayırıcı ile yaz (1234567 -> 1.234.567).
2. Para birimi bilinmiyorsa "TL" varsay.
3. En üstteki 3-5 satırdan somut örnek ver.
4. SQL veya teknik terim KULLANMA — iş dilinde konuş.
5. Sonuç boşsa açıkça belirt: "Verilen kriterlere uyan kayıt bulunamadı."
"""


# --------------------------------------------------------------------------- #
# Prompt builder'lar — node'ların LLM'e göndereceği mesaj listesi              #
# --------------------------------------------------------------------------- #
def format_schema_context(
    tables: list[str],
    describes: dict[str, list[dict]],
) -> str:
    """Şema bilgisini prompt'a gömülecek insan-okur metne çevirir.

    Args:
        tables: `list_tables` çıktısından tablo adları.
        describes: Her tablo için `describe_table` data listesi.

    Returns:
        Örnek: "Kullanılabilir tablolar:\\n- orders(order_id: BIGINT, revenue: DOUBLE)"
    """
    lines: list[str] = ["Kullanılabilir tablolar ve şemaları:"]
    for table in tables:
        cols = describes.get(table, [])
        col_desc = ", ".join(f"{c['name']}: {c['dtype']}" for c in cols)
        lines.append(f"- {table}({col_desc})")
    return "\n".join(lines)


def build_sql_generation_prompt(
    question: str,
    schema_context: str,
) -> list[dict[str, str]]:
    """SQL üretim node'u için mesaj listesi."""
    return [
        {"role": "system", "content": SQL_GENERATION_SYSTEM},
        {
            "role": "user",
            "content": f"{schema_context}\n\nSoru: {question}\n\nSQL:",
        },
    ]


def build_error_analysis_prompt(
    question: str,
    schema_context: str,
    failed_sql: str,
    error_type: str,
    error_message: str,
) -> list[dict[str, str]]:
    """Hata analizi node'u için mesaj listesi."""
    return [
        {"role": "system", "content": ERROR_ANALYSIS_SYSTEM},
        {
            "role": "user",
            "content": (
                f"{schema_context}\n\n"
                f"Orijinal soru: {question}\n\n"
                f"Başarısız SQL:\n{failed_sql}\n\n"
                f"Hata tipi: {error_type}\n"
                f"Hata mesajı: {error_message}\n\n"
                "Düzeltilmiş SQL:"
            ),
        },
    ]


def build_summarize_prompt(
    question: str,
    result: dict,
) -> list[dict[str, str]]:
    """Özetleme node'u için mesaj listesi."""
    return [
        {"role": "system", "content": SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": f"Soru: {question}\n\nSonuç (JSON):\n{result}\n\nCevap:",
        },
    ]