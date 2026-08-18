"""
scripts/load_to_postgres.py — Parquet dosyalarını PostgreSQL'e yükler.

Kullanım:
    python scripts/load_to_postgres.py             # data/processed altındaki tüm parquet'ler
    python scripts/load_to_postgres.py --drop       # tabloları sıfırdan yeniden oluştur
    python scripts/load_to_postgres.py --table orders customers  # sadece belirtilen tablolar

Bağlantı: POSTGRES_URL env değişkeninden okunur (.env dosyası).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import sqlalchemy
from sqlalchemy import text

ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import get_settings


# Parquet → PostgreSQL tip eşlemesi (pandas inference yeterli, ama tarih kolonları için override)
_DATE_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_date", "shipped_at", "delivered_at"],
    "payments": ["payment_date"],
    "refunds": ["refund_date"],
    "customers": ["created_at"],
}


def load_table(
    engine: sqlalchemy.Engine,
    parquet_path: Path,
    drop_if_exists: bool,
) -> dict[str, int | str]:
    table_name = parquet_path.stem
    start = time.perf_counter()

    df = pd.read_parquet(parquet_path)

    # Tarih kolonlarını datetime'a çevir (parquet'te zaten olabilir ama garanti et)
    for col in _DATE_COLUMNS.get(table_name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if drop_if_exists:
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

    df.to_sql(
        name=table_name,
        con=engine,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=500,
    )

    elapsed = round(time.perf_counter() - start, 2)
    return {"table": table_name, "rows": len(df), "cols": len(df.columns), "elapsed_s": elapsed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Parquet → PostgreSQL yükleyici")
    parser.add_argument("--drop", action="store_true", help="Tabloları önce drop et")
    parser.add_argument("--table", nargs="+", metavar="TABLE", help="Sadece bu tabloları yükle")
    args = parser.parse_args()

    s = get_settings()
    data_dir = s.data_dir

    parquet_files = sorted(data_dir.glob("*.parquet"))
    if not parquet_files:
        print(f"HATA: {data_dir} altında parquet dosyası bulunamadı.")
        sys.exit(1)

    if args.table:
        parquet_files = [p for p in parquet_files if p.stem in args.table]
        if not parquet_files:
            print(f"HATA: Belirtilen tablolar bulunamadı: {args.table}")
            sys.exit(1)

    postgres_url = s.postgres_url.get_secret_value()
    # SQLAlchemy için postgresql+psycopg formatına çevir
    if postgres_url.startswith("postgres://"):
        postgres_url = postgres_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif not postgres_url.startswith("postgresql"):
        postgres_url = f"postgresql+psycopg://{postgres_url}"

    engine = sqlalchemy.create_engine(postgres_url, pool_pre_ping=True)

    print(f"Hedef: PostgreSQL | Mod: {'DROP+REPLACE' if args.drop else 'REPLACE'}")
    print(f"Tablolar: {[p.stem for p in parquet_files]}")
    print("-" * 50)

    total_rows = 0
    for pf in parquet_files:
        print(f"  Yükleniyor: {pf.stem}...", end=" ", flush=True)
        try:
            result = load_table(engine, pf, drop_if_exists=args.drop)
            print(f"✓  {result['rows']:,} satır, {result['cols']} kolon — {result['elapsed_s']}s")
            total_rows += result["rows"]
        except Exception as exc:
            print(f"✗  HATA: {exc}")

    print("-" * 50)
    print(f"Tamamlandı. Toplam: {total_rows:,} satır yüklendi.")
    engine.dispose()


if __name__ == "__main__":
    main()