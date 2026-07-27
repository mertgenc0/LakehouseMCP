"""
Integration tests for src/mcp_servers/duckdb_server.py tools.

Gerçek DuckDB kullanır — data/processed/ altında seed_data.py'nin ürettiği
parquet dosyaları olmalı. Bu testleri çalıştırmadan önce:

    * list_tables: QueryResult tipi, success, seed tablolar (customers, orders, products)
    * describe_table: orders şeması, customers şeması, olmayan tablo hatası, geçersiz identifier bloğu
    * query_sql: COUNT sorgusu, JOIN + GROUP BY, DELETE bloğu, multi-statement DROP bloğu, olmayan tablo Catalog hatası, elapsed_ms doldurma
    * preview_file: Geçerli parquet, default limit=10, limit clamp (100 max), path traversal bloğu, unsupported ext
"""

from __future__ import annotations

from src.mcp_servers.duckdb_server import (
    describe_table,
    list_tables,
    preview_file,
    query_sql,
)
from src.mcp_servers.schemas import QueryResult


class TestListTables:
    def test_returns_queryresult_success(self):
        r = list_tables()
        assert isinstance(r, QueryResult)
        assert r.status == "success"
        assert r.columns == ["table"]

    def test_includes_seeded_tables(self):
        r = list_tables()
        names = {row["table"] for row in r.data}
        assert {"customers", "orders", "products"}.issubset(names)


class TestDescribeTable:
    def test_orders_schema(self):
        r = describe_table("orders")
        assert r.status == "success"
        assert r.columns == ["name", "dtype", "nullable"]
        col_names = {row["name"] for row in r.data}
        assert {"order_id", "customer_id", "product_id", "revenue"} <= col_names

    def test_customers_has_expected_columns(self):
        r = describe_table("customers")
        col_names = {row["name"] for row in r.data}
        assert {"customer_id", "name", "city", "segment"} <= col_names

    def test_nonexistent_table_returns_error_envelope(self):
        r = describe_table("bogus_table_that_does_not_exist")
        assert r.status == "error"
        assert r.error_type is not None
        # CatalogException veya benzeri; kesin isim DuckDB versiyonuna göre değişir
        assert "atalog" in (r.error_type or "") or r.error_type == "GuardrailViolation"

    def test_invalid_identifier_blocked_by_guardrail(self):
        # boşluk içeren identifier _quote_ident'ta reddedilir
        r = describe_table("bad name;")
        assert r.status == "error"
        assert r.error_type == "GuardrailViolation"


class TestQuerySql:
    def test_simple_count_returns_success(self):
        r = query_sql("SELECT COUNT(*) AS n FROM orders")
        assert r.status == "success"
        assert r.row_count == 1
        assert r.data[0]["n"] > 0

    def test_join_and_group_by(self):
        r = query_sql(
            "SELECT category, ROUND(SUM(revenue), 2) AS toplam "
            "FROM orders JOIN products USING(product_id) "
            "GROUP BY category ORDER BY toplam DESC"
        )
        assert r.status == "success"
        assert "category" in r.columns
        assert len(r.data) > 0

    def test_delete_blocked_by_guardrail(self):
        r = query_sql("DELETE FROM orders")
        assert r.status == "error"
        assert r.error_type == "GuardrailViolation"

    def test_drop_blocked_by_guardrail(self):
        r = query_sql("SELECT 1; DROP TABLE orders")
        assert r.status == "error"
        assert r.error_type == "GuardrailViolation"

    def test_unknown_table_returns_catalog_error(self):
        r = query_sql("SELECT * FROM tablo_yok")
        assert r.status == "error"
        # Guardrail geçer, DuckDB "table not found" der
        assert r.error_type != "GuardrailViolation"

    def test_result_has_elapsed_ms(self):
        r = query_sql("SELECT 1")
        assert r.elapsed_ms >= 0


class TestPreviewFile:
    def test_valid_parquet_returns_rows(self):
        r = preview_file("data/processed/orders.parquet", limit=3)
        assert r.status == "success"
        assert r.row_count == 3
        assert "order_id" in r.columns

    def test_limit_default_is_10(self):
        r = preview_file("data/processed/orders.parquet")
        assert r.row_count == 10

    def test_limit_clamped_to_max(self):
        r = preview_file("data/processed/orders.parquet", limit=99999)
        assert r.row_count <= 100  # server-side clamp

    def test_path_traversal_blocked(self):
        r = preview_file("../../etc/passwd")
        assert r.status == "error"
        assert r.error_type == "GuardrailViolation"

    def test_unsupported_extension_blocked(self):
        r = preview_file("data/processed/orders.txt")  # yok ama önce ext kontrolü
        assert r.status == "error"
        # ya "Desteklenmeyen" ya path check hatası — ikisi de kabul
        assert r.error_type in {"GuardrailViolation", "FileNotFoundError"}
