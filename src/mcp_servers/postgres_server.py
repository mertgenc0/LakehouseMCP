"""
PostgreSQL MCP server — read-only sorgulama.

*FastMCP + psycopg3. LLM/LangChain importu YOK
*Tool'lar hata durumunda exception fırlatmaz; QueryResult.fail(...) döner.

Ayrı süreç olarak çalışır:
    python -m src.mcp_servers.postgres_server
    npx @modelcontextprotocol/inspector python -m src.mcp_servers.postgres_server
"""

from __future__ import annotations

import time
from functools import lru_cache

import psycopg
from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.core.exceptions import GuardrailViolation
from src.core.guardrails import validate_sql
from src.core.logging import get_logger
from src.mcp_servers.schemas import ColumnInfo, QueryResult

logger = get_logger(__name__)
mcp = FastMCP()

#Setup
@lru_cache(maxsize=1)
def _connect() -> psycopg.Connection:
    """PostgreSQL bağlantıs - read only + statement timeout"""
    s = get_settings()
    #SQLAlchemy dialect prefix'ini kaldır
    url = s.postgres_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")
    conn = psycopg.connect(url, autocommit=False)
    with conn.cursor() as c:
        c.execute(f"SET statement_timeout = {s.postgres_statement_timeout_ms}")
        c.execute("SET default_transaction_read_only = on")
    conn.commit()
    logger.info("postgres_ready", schema=s.postgres_schema, timeout_ms=s.postgres_statement_timeout_ms)
    return conn

def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)

#Tools

@mcp.tool()
def list_tables() -> QueryResult:
    start = time.perf_counter()
    try:
        s = get_settings()
        conn = _connect()
        with conn.cursor() as c:
            c.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                "ORDER BY table_name",
                (s.postgres_schema,),
            )
            rows = c.fetchall()
        data = [{"table": r[0]} for r in rows]
        elapsed = _ms(start)
        logger.info("list_tables_ok", count=len(data), elapsed_ms=elapsed)
        return QueryResult.ok(data=data, columns=["table"], elapsed_ms=elapsed)
    except Exception as exc:
        elapsed = _ms(start)
        logger.exception("list_tables_failed", elapsed_ms=elapsed)
        return QueryResult.fail(
            error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed
        )

@mcp.tool()
def describe_table(table: str) -> QueryResult:
    """Tablonun kolon adı, tipi ve nullable bilgisini döner."""
    start = time.perf_counter()
    try:
        s = get_settings()
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "                                                                                                                                                                
                "WHERE table_schema = %s AND table_name = %s "                                                                                                                                                  
                "ORDER BY ordinal_position",
                (s.postgres_schema, table),
            )
            rows = cur.fetchall()
        if not rows:
            raise ValueError(f"Tablo bulunamadı: {s.postgres_schema}.{table}")
        data = [
            ColumnInfo(name=r[0], dtype=r[1], nullable=(r[2] == "YES")).model_dump()
            for r in rows
        ]
        elapsed = _ms(start)
        logger.info("describe_table_ok", table=table, columns=len(data), elapsed_ms=elapsed)
        return QueryResult.ok(
            data=data, columns=["name", "dtype", "nullable"], elapsed_ms=elapsed
        )
    except Exception as exc:
        elapsed = _ms(start)
        logger.exception("describe_table_failed", table=table, elapsed_ms=elapsed)
        return QueryResult.fail(
            error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed
        )


@mcp.tool()
def query_sql(sql: str) -> QueryResult:
    """Guardrail'dan geçirilmiş read-only SELECT sorgusunu çalıştırır."""
    start = time.perf_counter()
    try:
        safe_sql = validate_sql(sql)
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(safe_sql)
            columns = [d.name for d in cur.description or []]
            rows = cur.fetchall()
        data = [dict(zip(columns, row, strict=True)) for row in rows]
        elapsed = _ms(start)
        logger.info("query_ok", rows=len(data), elapsed_ms=elapsed, sql_preview=sql[:120])
        return QueryResult.ok(data=data, columns=columns, elapsed_ms=elapsed)
    except GuardrailViolation as exc:
        elapsed = _ms(start)
        logger.warning("guardrail_blocked", reason=exc.reason, sql_preview=sql[:120])
        return QueryResult.fail(
            error_type="GuardrailViolation", message=exc.reason, elapsed_ms=elapsed
        )
    except Exception as exc:
        elapsed = _ms(start)
        logger.exception("query_failed", elapsed_ms=elapsed, sql_preview=sql[:120])
        return QueryResult.fail(
            error_type=type(exc).__name__, message=str(exc), elapsed_ms=elapsed
        )

if __name__ == "__main__":
    mcp.run(transport="stdio")