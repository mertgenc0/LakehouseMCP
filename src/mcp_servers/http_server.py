"""
DuckDB MCP HTTP sunucusu — stateless Streamable HTTP transport.

Eski durum: Tek transport vardı: stdio. Her agent çağrısında yeni bir subprocess
başlatılırdı (duckdb_server.py). Bu stateful sayılır çünkü tek bir işlem, tek bir
bağlantı yönetir. Yatay ölçekleme imkânsız — iki instance aynı stdio kanalını paylaşamaz.

Yeni durum: Aynı tool'lar (list_tables, describe_table, query_sql, preview_file)
stateless Streamable HTTP üzerinden de sunuluyor. stateless_http=True şu anlama gelir:
her HTTP isteği tamamen bağımsızdır; sunucu istemci oturumunu bellekte tutmaz. Bu
sayede load balancer önüne N instance koyulabilir — her istek herhangi bir instance'a
gidebilir.

Çalıştırma (dev):
    python -m src.mcp_servers.http_server              # 127.0.0.1:8001
    MCP_DUCKDB_HTTP_PORT=9001 python -m src.mcp_servers.http_server

Prod (iki instance → stateless kanıtı):
    MCP_DUCKDB_HTTP_PORT=8001 python -m src.mcp_servers.http_server &
    MCP_DUCKDB_HTTP_PORT=8002 python -m src.mcp_servers.http_server &
    # Aynı istek her ikisine de gönderilebilir — tutarlı cevap döner.

Endpoint: POST /mcp  (Streamable HTTP spec, 2026-07-28)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.core.logging import get_logger
from src.mcp_servers.duckdb_server import describe_table, list_tables, preview_file, query_sql

log = get_logger(__name__, component="http_server")

_s = get_settings()

mcp_http = FastMCP(
    "duckdb-lakehouse-http",
    host=_s.mcp_duckdb_http_host,
    port=_s.mcp_duckdb_http_port,
    stateless_http=True,
)

# duckdb_server.py'deki tool fonksiyonlarını yeniden kaydeder.
# @mcp.tool() dekoratörü orijinal fonksiyonu değiştirmez — sadece stdio mcp
# instance'ına kaydeder. Burada aynı fonksiyonları HTTP instance'ına da tanıtıyoruz.
mcp_http.tool()(list_tables)
mcp_http.tool()(describe_table)
mcp_http.tool()(query_sql)
mcp_http.tool()(preview_file)


if __name__ == "__main__":
    log.info(
        "http_server_start",
        host=_s.mcp_duckdb_http_host,
        port=_s.mcp_duckdb_http_port,
        path="/mcp",
        stateless=True,
    )
    mcp_http.run(transport="streamable-http")