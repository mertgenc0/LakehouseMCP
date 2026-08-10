"""
Semantic layer — LLM'e gönderilen şema bağlamını iş bilgisiyle zenginleştirir.

Üç katman:
  1. Otomatik enum keşfi   — düşük kardinaliteli kolonların değerleri DuckDB'den çekilir
  2. Otomatik join çıkarımı — X_id kolon adı → X tablosu FK ilişkisi
  3. Manuel iş kuralları   — otomatik bulunamayan metrik formülleri ve önemli kurallar

Büyük sistemlerde bu dosyanın yerini dbt schema.yml veya bir data catalog alır;
bu proje için 7 tabloluk şemaya özel basit bir uygulama yeterli.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

# ---------------------------------------------------------------------------
# 1. Otomatik enum keşfi
# ---------------------------------------------------------------------------

# (tablo, kolon) çiftleri — kardinalite ≤ _MAX_CARDINALITY ise değerler enjekte edilir
_ENUM_CANDIDATES = [
    ("orders", "status"),
    ("orders", "channel"),
    ("payments", "payment_status"),
    ("payments", "payment_method"),
    ("customers", "segment"),
    ("customers", "country"),
]

_MAX_CARDINALITY = 20


def discover_enums(data_dir: Path) -> dict[str, list[str]]:
    """
    Parquet dosyalarından düşük kardinaliteli kolon değerlerini otomatik keşfeder.
    Sonuç: {'orders.status': ['cancelled', 'delivered', ...], ...}
    """
    con = duckdb.connect()
    result: dict[str, list[str]] = {}

    for table, col in _ENUM_CANDIDATES:
        parquet = data_dir / f"{table}.parquet"
        if not parquet.exists():
            continue
        try:
            rows = con.execute(
                f"SELECT DISTINCT {col} FROM read_parquet('{parquet}') "
                f"WHERE {col} IS NOT NULL ORDER BY {col}"
            ).fetchall()
            values = [str(r[0]) for r in rows]
            if len(values) <= _MAX_CARDINALITY:
                result[f"{table}.{col}"] = values
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# 2. Otomatik join çıkarımı
# ---------------------------------------------------------------------------

def infer_joins(schemas: dict[str, dict[str, list[dict[str, Any]]]]) -> list[str]:
    """
    Kolon adı pattern'inden (X_id → X tablosu) FK ilişkilerini çıkarır.

    Örnek:
        order_items.order_id   → orders.order_id
        order_items.product_id → products.product_id
        products.category_id   → categories.category_id
    """
    # Tüm kaynakların tablolarını birleştir
    all_tables: set[str] = set()
    for source_tables in schemas.values():
        all_tables.update(source_tables.keys())

    joins: list[str] = []
    seen: set[str] = set()

    for source_tables in schemas.values():
        for table, columns in source_tables.items():
            for col in columns:
                col_name: str = col.get("name", "")
                if not col_name.endswith("_id"):
                    continue
                if col_name == f"{table}_id":
                    # kendi primary key'i — atla
                    continue

                ref_base = col_name[:-3]  # "_id" kaldır
                for candidate in (ref_base, ref_base + "s"):
                    if candidate in all_tables and candidate != table:
                        key = f"{table}.{col_name}"
                        if key not in seen:
                            seen.add(key)
                            joins.append(f"{table}.{col_name} → {candidate}.{col_name}")
                        break

    return sorted(joins)


# ---------------------------------------------------------------------------
# 3. Manuel iş kuralları
# ---------------------------------------------------------------------------

# Otomatik bulunamayan formüller ve kritik join kalıpları
BUSINESS_METRICS: dict[str, str] = {
    "net_revenue": (
        "SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0))"
    ),
    "gross_margin": (
        "SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0))"
        " - SUM(oi.quantity * p.cost)"
    ),
    "aov (average order value)": (
        "net_revenue / COUNT(DISTINCT o.order_id)"
    ),
    "refund_rate": (
        "100.0 * COUNT(DISTINCT r.order_id) / COUNT(DISTINCT o.order_id)"
    ),
}

BUSINESS_RULES: list[str] = [
    "Ödenmemiş sipariş tespiti: orders LEFT JOIN payments ON order_id → WHERE payments.order_id IS NULL",
    "discount_amount %65 NULL olabilir — her zaman COALESCE(discount_amount, 0) kullan",
    "CLV / müşteri harcaması: status NOT IN ('cancelled', 'refunded') siparişleri dahil et",
    "Kategori hiyerarşisi: categories LEFT JOIN categories ON parent_category_id (self-join)",
    "Teslimat süresi: DATEDIFF('day', order_date, delivered_at) — sadece delivered_at IS NOT NULL satırlar",
]


# ---------------------------------------------------------------------------
# 4. Birleştirici — schema_discovery node'u bunu çağırır
# ---------------------------------------------------------------------------

def build_semantic_context(
    schemas: dict[str, dict[str, list[dict[str, Any]]]],
    data_dir: Path,
) -> str:
    """
    Enum değerleri + FK ilişkilerini prompt'a hazır metin olarak döner.

    BUSINESS_METRICS ve BUSINESS_RULES burada enjekte edilmez — büyük
    sistemlerde tüm metrikleri her soruda göndermek context'i patlatır.
    İleride RAG ile soru bazında ilgili metrikler seçilerek enjekte edilecek.
    """
    sections: list[str] = []

    enums = discover_enums(data_dir)
    if enums:
        lines = ["## Kolon Değer Kısıtları"]
        for col_key, values in enums.items():
            lines.append(f"- {col_key}: [{', '.join(repr(v) for v in values)}]")
        sections.append("\n".join(lines))

    joins = infer_joins(schemas)
    if joins:
        lines = ["## Tablo İlişkileri (FK)"]
        for j in joins:
            lines.append(f"- {j}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)