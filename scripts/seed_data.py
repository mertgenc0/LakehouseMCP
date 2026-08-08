"""seed_data.py — Karmaşık e-ticaret veri seti üretici.

Ajanın gerçek text-to-SQL yeteneğini test etmek için bilinçli olarak
"zorluk kaynakları" içeren 7 tablolu bir yıldız şeması üretir:

    categories ──(self-join: parent_category_id)
        ▲
        │
    products ──┐
               │
    order_items ──► orders ──► customers
                       │
                       ├──► payments   (bazı siparişlerde YOK  → LEFT JOIN)
                       └──► refunds    (seyrek               → LEFT JOIN)

Bilinçli zorluk kaynakları
--------------------------
* LEFT JOIN zorunluluğu : ödenmemiş siparişlerin payments kaydı yoktur;
                          iade sadece küçük bir alt kümede vardır.
* COALESCE gereği       : order_items.discount_amount ve payments.amount
                          NULL olabilir → toplamlarda COALESCE(...,0) şart.
* NULL semantiği        : shipped_at (kargolanmadıysa NULL),
                          delivered_at, refunded_at hep sparse.
* Self-join             : categories.parent_category_id → categories.category_id.
* Enum durum makinesi   : order.status = pending|paid|shipped|delivered|
                          cancelled|refunded  ile payment.payment_status
                          tutarlı ama farklı granülerlikte.
* Zaman bazlı gruplama  : order_date 2023-01 .. 2025-12 arasına yayılır.

Çıktı: DATA_DIR (varsayılan ./data/processed) altına 7 adet Parquet dosyası
+ küçük bir schema_manifest.json (semantic layer'ın çekirdeği).

Çalıştırma:
    python scripts/seed_data.py                 # varsayılan boyut
    python scripts/seed_data.py --scale 3        # 3x büyük
    python scripts/seed_data.py --seed 7         # tekrarlanabilir farklı veri
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

# --------------------------------------------------------------------------- #
# Sabitler (gerçek dünya dağılımını taklit eden ağırlıklar)
# --------------------------------------------------------------------------- #

COUNTRIES = [
    ("TR", ["Istanbul", "Ankara", "Izmir", "Bursa", "Antalya"]),
    ("DE", ["Berlin", "Munich", "Hamburg"]),
    ("US", ["New York", "San Francisco", "Austin"]),
    ("GB", ["London", "Manchester"]),
    ("FR", ["Paris", "Lyon"]),
]
SEGMENTS = ["consumer", "smb", "enterprise"]
SEGMENT_WEIGHTS = [0.70, 0.22, 0.08]

CHANNELS = ["web", "mobile_app", "marketplace", "phone"]
CHANNEL_WEIGHTS = [0.45, 0.35, 0.15, 0.05]

PAYMENT_METHODS = ["credit_card", "paypal", "bank_transfer", "gift_card"]

# order.status durum makinesi ve olasılıkları
# pending  : ödeme bekliyor  (payments YOK)   → LEFT JOIN testi
# paid     : ödendi, kargolanmadı (shipped_at NULL)
# shipped  : kargoda
# delivered: teslim
# cancelled: iptal (payments olabilir/olmayabilir)
# refunded : iade edildi (refunds kaydı VAR)
ORDER_STATUS = ["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]
ORDER_STATUS_WEIGHTS = [0.08, 0.10, 0.12, 0.55, 0.08, 0.07]

# Kategori hiyerarşisi: (isim, parent_isim|None)
CATEGORY_TREE = [
    ("Electronics", None),
    ("Computers", "Electronics"),
    ("Laptops", "Computers"),
    ("Monitors", "Computers"),
    ("Phones", "Electronics"),
    ("Home", None),
    ("Kitchen", "Home"),
    ("Furniture", "Home"),
    ("Fashion", None),
    ("Men", "Fashion"),
    ("Women", "Fashion"),
    ("Sports", None),
    ("Outdoor", "Sports"),
]


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #

def _rand_date(rng: random.Random, start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=rng.randint(0, delta))


def _weighted(rng: random.Random, items: list, weights: list):
    return rng.choices(items, weights=weights, k=1)[0]


# --------------------------------------------------------------------------- #
# Tablo üreticileri
# --------------------------------------------------------------------------- #

def build_categories() -> pl.DataFrame:
    name_to_id = {name: i + 1 for i, (name, _) in enumerate(CATEGORY_TREE)}
    rows = []
    for name, parent in CATEGORY_TREE:
        rows.append(
            {
                "category_id": name_to_id[name],
                "category_name": name,
                # self-join hedefi: kök kategorilerde NULL
                "parent_category_id": name_to_id.get(parent) if parent else None,
            }
        )
    return pl.DataFrame(rows)


def build_customers(rng: random.Random, n: int) -> pl.DataFrame:
    rows = []
    for cid in range(1, n + 1):
        country, cities = _weighted(rng, COUNTRIES, [0.4, 0.15, 0.2, 0.15, 0.1])
        rows.append(
            {
                "customer_id": cid,
                "full_name": f"Customer {cid:05d}",
                "country": country,
                "city": rng.choice(cities),
                "segment": _weighted(rng, SEGMENTS, SEGMENT_WEIGHTS),
                "signup_date": _rand_date(rng, date(2022, 1, 1), date(2025, 6, 30)),
                # bazı müşterilerin hiç siparişi olmayacak → LEFT JOIN testi (aşağıda garanti edilir)
            }
        )
    return pl.DataFrame(rows)


def build_products(rng: random.Random, categories: pl.DataFrame, n: int) -> pl.DataFrame:
    # sadece "yaprak" kategorilere ürün ata (gerçekçi)
    leaf_ids = [
        r["category_id"]
        for r in categories.iter_rows(named=True)
        if r["category_id"] not in categories["parent_category_id"].to_list()
    ]
    rows = []
    for pid in range(1, n + 1):
        cost = round(rng.uniform(5, 800), 2)
        margin = rng.uniform(1.15, 2.4)
        rows.append(
            {
                "product_id": pid,
                "product_name": f"Product-{pid:04d}",
                "category_id": rng.choice(leaf_ids),
                "unit_price": round(cost * margin, 2),
                "cost": cost,
                "is_active": rng.random() > 0.12,  # %12 pasif
            }
        )
    return pl.DataFrame(rows)


def build_orders(
    rng: random.Random, customers: pl.DataFrame, n: int
) -> pl.DataFrame:
    cust_ids = customers["customer_id"].to_list()
    # ilk 5% müşteriyi "hiç sipariş vermeyen" olarak ayır → LEFT JOIN testi
    no_order_cutoff = max(1, len(cust_ids) // 20)
    ordering_customers = cust_ids[no_order_cutoff:]

    rows = []
    for oid in range(1, n + 1):
        status = _weighted(rng, ORDER_STATUS, ORDER_STATUS_WEIGHTS)
        order_dt = _rand_date(rng, date(2023, 1, 1), date(2025, 12, 20))

        shipped_at = None
        delivered_at = None
        if status in ("shipped", "delivered", "refunded"):
            shipped_at = datetime.combine(order_dt, datetime.min.time()) + timedelta(
                days=rng.randint(1, 4), hours=rng.randint(0, 23)
            )
        if status in ("delivered", "refunded"):
            delivered_at = shipped_at + timedelta(days=rng.randint(1, 6))

        rows.append(
            {
                "order_id": oid,
                "customer_id": rng.choice(ordering_customers),
                "order_date": order_dt,
                "status": status,
                "channel": _weighted(rng, CHANNELS, CHANNEL_WEIGHTS),
                "shipped_at": shipped_at,      # NULL olabilir
                "delivered_at": delivered_at,  # NULL olabilir
            }
        )
    return pl.DataFrame(rows)


def build_order_items(
    rng: random.Random, orders: pl.DataFrame, products: pl.DataFrame
) -> pl.DataFrame:
    prod = {r["product_id"]: r for r in products.iter_rows(named=True)}
    prod_ids = list(prod.keys())
    rows = []
    item_id = 1
    for o in orders.iter_rows(named=True):
        # iptal edilen siparişlerde bazen 1 satır, normalde 1-5 satır
        n_items = 1 if o["status"] == "cancelled" else rng.randint(1, 5)
        chosen = rng.sample(prod_ids, k=min(n_items, len(prod_ids)))
        for pid in chosen:
            qty = rng.randint(1, 6)
            unit_price = prod[pid]["unit_price"]
            # %65 satırda indirim YOK → NULL bırak (COALESCE testi)
            discount = None
            if rng.random() < 0.35:
                discount = round(unit_price * qty * rng.uniform(0.05, 0.30), 2)
            rows.append(
                {
                    "order_item_id": item_id,
                    "order_id": o["order_id"],
                    "product_id": pid,
                    "quantity": qty,
                    "unit_price": unit_price,      # sipariş anındaki fiyat (snapshot)
                    "discount_amount": discount,   # NULL olabilir → COALESCE
                }
            )
            item_id += 1
    return pl.DataFrame(rows)


def build_payments(rng: random.Random, orders: pl.DataFrame) -> pl.DataFrame:
    """Sadece ödenmiş/ilerlemiş siparişlerde payment vardır.

    'pending' siparişlerde payment YOK → orders LEFT JOIN payments testi.
    """
    rows = []
    pay_id = 1
    for o in orders.iter_rows(named=True):
        if o["status"] == "pending":
            continue  # ödeme yok
        if o["status"] == "cancelled" and rng.random() < 0.5:
            continue  # iptallerin yarısında ödeme hiç alınmamış
        paid_at = datetime.combine(o["order_date"], datetime.min.time()) + timedelta(
            hours=rng.randint(0, 48)
        )
        pay_status = "captured"
        if o["status"] == "refunded":
            pay_status = "refunded"
        elif o["status"] == "cancelled":
            pay_status = "voided"
        rows.append(
            {
                "payment_id": pay_id,
                "order_id": o["order_id"],
                "payment_method": rng.choice(PAYMENT_METHODS),
                "amount": None if rng.random() < 0.03 else 0.0,  # 0.0 placeholder, aşağıda doldurulur
                "payment_status": pay_status,
                "paid_at": paid_at,
            }
        )
        pay_id += 1
    return pl.DataFrame(rows)


def build_refunds(rng: random.Random, orders: pl.DataFrame) -> pl.DataFrame:
    """Yalnızca status='refunded' siparişlerde kayıt → çok seyrek LEFT JOIN."""
    reasons = ["defective", "wrong_item", "changed_mind", "late_delivery", "other"]
    rows = []
    rid = 1
    for o in orders.iter_rows(named=True):
        if o["status"] != "refunded":
            continue
        refunded_at = (o["delivered_at"] or datetime.combine(
            o["order_date"], datetime.min.time()
        )) + timedelta(days=rng.randint(1, 14))
        rows.append(
            {
                "refund_id": rid,
                "order_id": o["order_id"],
                "reason": rng.choice(reasons),
                "amount": 0.0,  # aşağıda gerçek tutara göre doldurulur
                "refunded_at": refunded_at,
            }
        )
        rid += 1
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Tutar tutarlılığı: payment.amount ve refund.amount'u order_items'tan türet
# --------------------------------------------------------------------------- #

def reconcile_amounts(
    order_items: pl.DataFrame,
    payments: pl.DataFrame,
    refunds: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    # Sipariş bazında net tutar = SUM(qty*unit_price - COALESCE(discount,0))
    order_totals = (
        order_items.with_columns(
            (
                pl.col("quantity") * pl.col("unit_price")
                - pl.col("discount_amount").fill_null(0.0)
            ).alias("line_net")
        )
        .group_by("order_id")
        .agg(pl.col("line_net").sum().round(2).alias("order_net"))
    )
    totals_map = {r["order_id"]: r["order_net"] for r in order_totals.iter_rows(named=True)}

    # payments.amount: NULL bırakılmışları (%3) NULL tut (COALESCE testi), gerisini doldur
    payments = payments.with_columns(
        pl.when(pl.col("amount").is_null())
        .then(None)
        .otherwise(
            pl.col("order_id").map_elements(
                lambda oid: totals_map.get(oid, 0.0), return_dtype=pl.Float64
            )
        )
        .alias("amount")
    )

    # refund.amount: net tutarın tamamı ya da bir kısmı
    refunds = refunds.with_columns(
        pl.col("order_id")
        .map_elements(lambda oid: round(totals_map.get(oid, 0.0), 2), return_dtype=pl.Float64)
        .alias("amount")
    )
    return payments, refunds


# --------------------------------------------------------------------------- #
# Manifest (semantic layer çekirdeği — Dalga 3 burada beslenecek)
# --------------------------------------------------------------------------- #

SCHEMA_MANIFEST = {
    "tables": {
        "customers": "Müşteri ana verisi. Bazı müşterilerin hiç siparişi yoktur (LEFT JOIN).",
        "categories": "Kategori hiyerarşisi. parent_category_id kök kategorilerde NULL (self-join).",
        "products": "Ürün kataloğu. is_active=false pasif ürünler.",
        "orders": "Sipariş başlığı. status durum makinesi; shipped_at/delivered_at NULL olabilir.",
        "order_items": "Sipariş satırları. discount_amount NULL olabilir → COALESCE(...,0).",
        "payments": "Ödeme. pending siparişlerde KAYIT YOK. amount bazen NULL.",
        "refunds": "İade. Sadece status='refunded' siparişlerde kayıt.",
    },
    "metrics": {
        "net_revenue": "SUM(order_items.quantity*order_items.unit_price - COALESCE(order_items.discount_amount,0))",
        "gross_margin": "net_revenue - SUM(order_items.quantity*products.cost)",
        "aov": "net_revenue / COUNT(DISTINCT orders.order_id)",
        "refund_rate": "COUNT(refunds.order_id) / COUNT(DISTINCT orders.order_id)",
        "unpaid_orders": "orders LEFT JOIN payments WHERE payments.order_id IS NULL",
    },
    "join_hints": {
        "orders->customers": "orders.customer_id = customers.customer_id (INNER)",
        "orders->payments": "orders.order_id = payments.order_id (LEFT — pending'lerde yok)",
        "orders->refunds": "orders.order_id = refunds.order_id (LEFT — seyrek)",
        "order_items->products": "order_items.product_id = products.product_id (INNER)",
        "products->categories": "products.category_id = categories.category_id (INNER)",
        "categories self": "categories.parent_category_id = categories.category_id (LEFT self-join)",
    },
}


# --------------------------------------------------------------------------- #
# Ana akış
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Karmaşık e-ticaret seed üretici")
    parser.add_argument("--scale", type=float, default=1.0, help="veri boyutu çarpanı")
    parser.add_argument("--seed", type=int, default=42, help="rastgelelik tohumu")
    parser.add_argument(
        "--out", type=str, default="./data/processed", help="Parquet çıktı dizini"
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    s = args.scale

    n_customers = int(1_000 * s)
    n_products = int(300 * s)
    n_orders = int(5_000 * s)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"→ Üretiliyor (scale={s}, seed={args.seed}) …")
    categories = build_categories()
    customers = build_customers(rng, n_customers)
    products = build_products(rng, categories, n_products)
    orders = build_orders(rng, customers, n_orders)
    order_items = build_order_items(rng, orders, products)
    payments = build_payments(rng, orders)
    refunds = build_refunds(rng, orders)
    payments, refunds = reconcile_amounts(order_items, payments, refunds)

    tables = {
        "categories": categories,
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "payments": payments,
        "refunds": refunds,
    }
    for name, df in tables.items():
        path = out / f"{name}.parquet"
        df.write_parquet(path)
        print(f"   ✓ {name:<12} {df.height:>6} satır  → {path}")

    (out / "schema_manifest.json").write_text(
        json.dumps(SCHEMA_MANIFEST, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"   ✓ schema_manifest.json  → {out / 'schema_manifest.json'}")
    print("→ Bitti.")


if __name__ == "__main__":
    main()
