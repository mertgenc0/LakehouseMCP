##-- DATA_DIR altındaki Parquet/CSV dosyalarını view olarak kaydeder ve MCP protokolü üzerinden 4 tool açar: list_tables, describe_table, query_sql, preview_file.--#

"""
DuckDB MCP server — Parquet/CSV sorgulama.

FastMCP + DuckDB. LLM/LangChain importu yok
Tool'lar hata durumunda exception fırlatmaz; QueryResult.fail(...) döner.

Ayrı süreç olarak çalışır:
    python -m src.mcp_servers.duckdb_server
    npx @modelcontextprotocol/inspector python -m src.mcp_servers.duckdb_server
"""

from __future__ import annotations

# FastMCP() -> Model Context Protocol sunucusunu kolayca kurmayı, tool'ları kaydetmeyi ve stdio üzerinden haberleşmeyi sağlayan yüksek seviyeli kütüphanedir.
import time
from functools import lru_cache
from pathlib import Path

import duckdb
from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.core.exceptions import GuardrailViolation
from src.core.guardrails import validate_path, validate_sql
from src.core.logging import get_logger
from src.mcp_servers.schemas import ColumnInfo, QueryResult

log = get_logger(__name__, component="duckdb_server")
mcp = FastMCP("duckdb-lakehouse")

_SUPPORTED_EXT: dict[str, str] = {
    ".parquet": "parquet_scan",
    ".csv": "read_csv_auto",
}

@lru_cache(maxsize=1)
def _connect() -> duckdb.DuckDBPyConnection:
    """
    DuckDB bağlantısını kurar ve DATA_DIR altındaki dosyaları view yapar.
    Süreç ömrü boyunca tek bağlantı (lru_cache = singleton).
    """

    settings = get_settings()
    conn = duckdb.connect(settings.duckdb_path)
    """PRAGMA -> DuckDB'nin kullanacağı CPU çekirdek sayısını ve maksimum bellek (RAM) limitini config.py üzerinden sınırlandırmak için kullanılır."""
    conn.execute(f"PRAGMA threads={settings.duckdb_threads}")
    conn.execute(f"PRAGMA memory_limit='{settings.duckdb_memory_limt}'")

    registered = _register_views(conn, settings.data_dir)
    log.info("duckdb_ready", data_dir=str(settings.data_dir), views=registered)
    return conn


def _register_views(conn: duckdb.DuckDBPyConnection, data_dir: Path) -> list[str]:
    """
    DATA_DIR altındaki .parquet/.csv dosyalarını view olarak kaydeder.
    Dosya adı (uzantısız) view adı olur: sales.parquet -> view `sales`.
    """
    registered: list[str] = []
    if not data_dir.exists():
        return registered

    for path in sorted(data_dir.iterdir()):
        if not path.is_file():
            continue
        reader = _SUPPORTED_EXT.get(path.suffix.lower())
        if reader is None:
            continue

        view_name = path.stem
        # _quote_ident + DATA_DIR altı path'ler; kullanıcı input'u değil
        conn.execute(
            f"CREATE OR REPLACE VIEW {_quote_ident(view_name)} AS "  # noqa: S608
            f"SELECT * FROM {reader}('{path.as_posix()}')"
        )
        registered.append(view_name)
    return registered


def _quote_ident(identifier: str) -> str:
    """SQL identifier'ını çift tırnakla sarar; içerik alnum + '_' değilse reddeder."""
    if not identifier or not identifier.replace("_", "").isalnum():
        raise GuardrailViolation(f"Geçersiz identifier: {identifier}")
    return f'"{identifier}"'


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)

#@mcp.tool -> Altındaki Python fonksiyonunu dış dünyaya MCP Protokolü standardında bir "Tool (Araç)" olarak açar.MCP şemasına otomatik dönüşütrür.
@mcp.tool()
def list_tables() -> QueryResult:
    """DATA_DIR altında kayıtlı tabloları döner."""
    start = time.perf_counter()

    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type = 'VIEW' ORDER BY table_name"
        ).fetchall()
        data = [{"table": r[0]} for r in rows]
        elapsed = _ms(start)
        log.info("list_tables_ok", count=len(data), elapsed_ms=elapsed)
        return QueryResult.ok(data=data, columns=["table"], elapsed_ms=elapsed)

    except Exception as exc:
        elapsed = _ms(start)
        log.exception("list_tables_failed", elapsed_ms=elapsed)
        return QueryResult.fail(error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed)

#@mcp.tool -> Altındaki Python fonksiyonunu dış dünyaya MCP Protokolü standardında bir "Tool (Araç)" olarak açar.MCP şemasına otomatik dönüşütrür.
@mcp.tool()
def describe_table(table: str) -> QueryResult:
    """Verilen tablonun kolon şemasını döner.

    Args:
        table: Kayıtlı view adı (list_tables çıktısından).
    """
    start = time.perf_counter()#işlemin ne kadar sürdüğü hesaplnaması için

    try:
        conn = _connect()
        rows = conn.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()
        data = [
            ColumnInfo(
                name=r[0],
                dtype=str(r[1]),
                nullable=(str(r[2]).upper() == "YES"),
            ).model_dump()
            for r in rows
        ]
        elapsed = _ms(start)
        log.info("describe_table_ok", table=table, columns=len(data), elapsed_ms=elapsed)
        return QueryResult.ok(data=data, columns=["name", "dtype", "nullable"], elapsed_ms=elapsed)

    except GuardrailViolation as exc:
        return QueryResult.fail(
            error_type="GuardrailViolation", message=exc.reason, elapsed_ms=_ms(start)
        )

    except Exception as exc:
        elapsed = _ms(start)
        log.exception("describe_table_failed", table=table, elapsed_ms=elapsed)
        return QueryResult.fail(error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed)

#@mcp.tool -> Altındaki Python fonksiyonunu dış dünyaya MCP Protokolü standardında bir "Tool (Araç)" olarak açar.MCP şemasına otomatik dönüşütrür.
@mcp.tool()
def query_sql(sql: str) -> QueryResult:
    """Kullanıcının SQL sorgusunu guardrail'dan geçirip çalıştırır.

    Args:
        sql: SELECT/WITH ile başlayan SQL.
    """
    start = time.perf_counter()

    try:
        safe_sql = validate_sql(sql)
        conn = _connect()
        cursor = conn.execute(safe_sql)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchall()
        data = [dict(zip(columns, row, strict=True)) for row in rows]
        elapsed = _ms(start)
        log.info("query_ok", rows=len(data), elapsed_ms=elapsed, sql_preview=sql[:120])

        return QueryResult.ok(data=data, columns=columns, elapsed_ms=elapsed)
    except GuardrailViolation as exc:
        elapsed = _ms(start)
        log.warning("guardrail_blocked", reason=exc.reason, sql_preview=sql[:120])
        return QueryResult.fail(
            error_type="GuardrailViolation", message=exc.reason, elapsed_ms=elapsed
        )

    except Exception as exc:
        elapsed = _ms(start)
        log.exception("query_failed", elapsed_ms=elapsed, sql_preview=sql[:120])
        return QueryResult.fail(error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed)

#@mcp.tool -> Altındaki Python fonksiyonunu dış dünyaya MCP Protokolü standardında bir "Tool (Araç)" olarak açar.MCP şemasına otomatik dönüşütrür.
@mcp.tool()
def preview_file(path: str, limit: int = 10) -> QueryResult:
    """
    DATA_DIR altındaki bir dosyanın ilk N satırını gösterir.
    Args:
        path: Dosya yolu (DATA_DIR altında olmalı).
        limit: Kaç satır (1-100 arası, varsayılan 10).
    """
    start = time.perf_counter()

    try:
        safe_path = validate_path(path)
        reader = _SUPPORTED_EXT.get(safe_path.suffix.lower())
        if reader is None:
            raise GuardrailViolation(f"Desteklenmeyen dosya türü: {safe_path.suffix}")
        limit = max(1, min(int(limit), 100))
        conn = _connect()
        # validate_path DATA_DIR'i doğruladı, limit int'e cast edildi
        cursor = conn.execute(
            f"SELECT * FROM {reader}('{safe_path.as_posix()}') LIMIT {limit}"  # noqa: S608
        )
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchall()
        data = [dict(zip(columns, row, strict=True)) for row in rows]
        elapsed = _ms(start)
        log.info("preview_ok", path=str(safe_path), rows=len(data), elapsed_ms=elapsed)
        return QueryResult.ok(data=data, columns=columns, elapsed_ms=elapsed)

    except GuardrailViolation as exc:
        elapsed = _ms(start)
        log.warning("preview_blocked", reason=exc.reason, path=path)
        return QueryResult.fail(
            error_type="GuardrailViolation", message=exc.reason, elapsed_ms=elapsed
        )

    except Exception as exc:
        elapsed = _ms(start)
        log.exception("preview_failed", path=path, elapsed_ms=elapsed)
        return QueryResult.fail(error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed)


if __name__ == "__main__":
    mcp.run(transport="stdio")
