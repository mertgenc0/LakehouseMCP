"""
LangGraph agent sistem prompt'ları + prompt builder fonksiyonları.

Her prompt bir node'a hizmet eder. Prompt metni değişirse davranış değişir —
kod dokunmadan prompt tune etme kapısı burası.
"""

from __future__ import annotations

from typing import Any

# System prompts — LLM'in "rol + görev + kural" seti                          #
SQL_GENERATION_SYSTEM = """
Sen bir data analytics uzmanısın. Kullanıcının Türkçe/İngilizce sorusunu, verilen
şema bilgisine bakarak TEK bir SELECT sorgusuna çevirirsin.

Şema BİRDEN FAZLA veri kaynağı içerebilir: `[DUCKDB]` ve `[POSTGRES]`. İkisi de
aynı tabloları içerebilir. Sen soruya en uygun kaynağı seç.

KURALLAR:
1. Yanıtın İLK SATIRI şu formatta olmalı: `-- SOURCE: duckdb` veya `-- SOURCE: postgres`
2. İkinci satırdan itibaren SADECE SQL — SELECT veya WITH ile başla.
3. INSERT/UPDATE/DELETE/DDL YASAK.
4. Yalnızca seçtiğin kaynağın şemasındaki tabloları ve kolonları kullan.
5. Kolon adlarını AYNEN kullan (case-sensitive).
6. `SELECT *` yerine ihtiyaç duyulan kolonları açıkça listele.
7. Aggregate (SUM/COUNT/AVG) sonuçlarını 2 basamağa yuvarla: `ROUND(x, 2)`.
8. SQL kısmına açıklama, kod fence, ek yorum EKLEME.

ÖRNEK ÇIKTI:
-- SOURCE: duckdb
SELECT category, ROUND(SUM(revenue), 2) AS toplam
FROM orders JOIN products USING(product_id)
GROUP BY category
"""

ERROR_ANALYSIS_SYSTEM = """
Sen bir SQL hata düzeltme uzmanısın. Aşağıda başarısız bir SQL, veritabanı hata
mesajı ve orijinal soru var. Görevin: hatayı analiz edip DÜZELTİLMİŞ SQL üretmek.

STRATEJİ:
1. Hata tipini oku (CatalogException = tablo/kolon yok; ParserException = syntax; ...).
2. Şema bilgisine dönüp gerçek kolon/tablo adlarını doğrula.
3. Gerekirse kaynağı değiştir — belki tablo diğer kaynakta var.
4. Sadece bozuk kısmı düzelt — sorgunun geri kalanını KORU.
5. Yanıtın İLK SATIRI `-- SOURCE: duckdb` veya `-- SOURCE: postgres` olmalı.
6. İkinci satırdan itibaren düzeltilmiş SQL — açıklama YOK.
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

# Prompt builder'lar — node'ların LLM'e göndereceği mesaj listesi
def format_schema_context(schemas: dict[str, dict[str, list[dict[str, Any]]]],) -> str:
    """Çok-kaynaklı şema bilgisini prompt'a gömülecek metne çevirir.
    Args:
        schemas: {'duckdb': {'orders': [{'name': 'id', 'dtype': 'BIGINT'}, ...]},
                  'postgres': {'orders': [...]}}

    Returns:
        `[DUCKDB]` ve `[POSTGRES]` başlıkları altında gruplanmış tablo listesi.
    """
    if not schemas:
        return "Kullanılabilir kaynak yok."

    lines: list[str] = ["Kullanılabilir veri kaynakları ve şemaları:"]
    for source in sorted(schemas):
        tables = schemas[source]
        lines.append(f"\n[{source.upper()}]")
        if not tables:
            lines.append("- (bu kaynakta tablo yok)")
            continue
        for table in sorted(tables):
            cols = tables[table]
            col_desc = ", ".join(f"{c['name']}: {c['dtype']}" for c in cols)
            lines.append(f"- {table}({col_desc})")
    return "\n".join(lines)

def build_sql_generation_prompt(question: str, schema_context: str,) -> list[dict[str, str]]:
    """SQL üretim node'u için mesaj listesi."""
    return [
        {"role": "system", "content": SQL_GENERATION_SYSTEM},
        {
            "role": "user",
            "content": f"{schema_context}\n\nSoru: {question}\n\nSQL:",
        },
    ]

def build_error_analysis_prompt(question: str, schema_context: str, failed_sql: str, error_type: str, error_message: str,) -> list[dict[str, str]]:
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

def build_summarize_prompt(question: str,result: dict[str, Any],) -> list[dict[str, str]]:
    """Özetleme node'u için mesaj listesi."""
    return [
        {"role": "system", "content": SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": f"Soru: {question}\n\nSonuç (JSON):\n{result}\n\nCevap:",
        },
    ]
