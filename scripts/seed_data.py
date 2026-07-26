##--DATA_DIR'e demo/test için üç adet Parquet dosyası üretir (customers, products, orders), böylece MCP sunucusunun list_tables/query_sql tool'ları gerçek veriyle test edilebilir.--##

"""
Seed data generator — DATA_DIR'e örnek Parquet dosyaları üretir.

Kullanım:
      python scripts/seed_data.py                     # varsayılan boyutlar
      python scripts/seed_data.py --orders 5000       # daha büyük fact tablosu
      python scripts/seed_data.py --force             # varolan dosyaları üzerine yaz
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
import polars as pl
from src.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__)

CITIES = ["İstanbul", "Ankara", "İzmir", "Bursa", "Antalya", "Adana", "Konya", "Gaziantep", "Sivas", "Bursa"]
SEGMENTS = ["Bireysel", "KOBİ", "Kurumsal"]
CATEGORIES = ["Elektronik", "Giyim", "Kitap", "Ev & Yaşam", "Spor", "Kozmetik", "Ayakkabi", "Oyun"]


def _customers(n: int, rng: random.Random) -> pl.DataFrame:
    return pl.DataFrame({
        "customer_id": list(range(1, n + 1)),
        "name": [f"Müşteri {i:04d}" for i in range(1, n + 1)],
        "city": [rng.choice(CITIES) for _ in range(n)],
        "segment": [rng.choice(SEGMENTS) for _ in range(n)],
        "signup_date": [
            date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
            for _ in range(n)
        ],
    })

def _products(n: int, rng: random.Random) -> pl.DataFrame:
    return pl.DataFrame({
        "product_id": list(range(1, n + 1)),
        "name": [f"Ürün {i:03d}" for i in range(1, n + 1)],
        "category": [rng.choice(CATEGORIES) for _ in range(n)],
        "unit_price": [round(rng.randint(10, 50000), 2) for _ in range(n)],
    })

def _orders(n: int, customer_ids: list[int], products: pl.DataFrame, rng: random.Random) -> pl.DataFrame:
    prod_ids = products["product_id"].to_list()
    prices = dict(zip(prod_ids, products["unit_price"].to_list()))
    rows = []
    for i in range(1, n + 1):
        pid = rng.choice(prod_ids)
        qty = rng.randint(1, 5)
        rows.append({
            "order_id": i,
            "customer_id": rng.choice(customer_ids),
            "product_id": pid,
            "quantity": qty,
            "order_date": date(2024, 1, 1) + timedelta(days=rng.randint(0, 500)),
            "revenue": round(prices[pid] * qty, 2)
        })
    return pl.DataFrame(rows)

def main() -> None:
    parser = argparse.ArgumentParser(description="Sample Parquet üretic.")
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--products", type=int, default=50)
    parser.add_argument("--orders", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true", help="Force overwrite existing data")
    args = parser.parse_args()

    settings = get_settings()
    out = settings.data_dir
    out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    customers = _customers(args.customers, rng)
    products = _products(args.products, rng)
    orders = _orders(args.orders, customers["customer_id"].to_list(), products, rng)

    for name, df in ("customers", customers), ("products", products), ("orders", orders):
        target = out / f"{name}.parquet"
        if target.exists() and not args.force:
            log.warning("skip_existing", path=str(target), hint="--force ile üzerine koy")
            continue
        df.write_parquet(target)
        log.info("wrote_parquet", table= name, rows = df.height, path = str(target))

if __name__ == "__main__":
    main()
