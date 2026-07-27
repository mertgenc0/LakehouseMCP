##-LangGraph agent'ının MCP sunucusuyla konuşacağı async client. Sürecin ayağa kalkması, stdio session'ın açılıp kapanması, tool çağrılarının timeout ve hata yönetimi hep bu class'ta.-##

"""
Mimarideki yeri:
  - Şu ana kadar MCP sunucusunu Inspector ile test ettik — Inspector, bizim yerimize client rolü oynadı.
  - Şimdi Inspector'ın yaptığını kod ile yapıyoruz. Agent'ımız Inspector'ın kodla versiyonu.
  - Agent doğrudan subprocess.Popen(...) veya duckdb.connect(...) çağırmaz — sadece bu client üzerinden konuşur
  - İleride Postgres server eklendiğinde aynı class'ı postgres_client() factory ile yeniden kullanacağız.
"""
from __future__ import annotations

import asyncio
import json

from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from src.config import get_settings
from src.core.exceptions import MCPConnectionError
from src.core.logging import get_logger

logger = get_logger(__name__, component= "mcp_client")

class MCPClient:
    """
    MCP stdio client — bir sunucu sürecine bağlanır, tool çağırır.
    Async context manager olarak kullanılır; süreç ve bağlantı otomatik yönetilir.
    """

    def __init__(self, name: str, command: str, args: list[str], timeout: int=60) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.timeout = timeout
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "MCPClient":
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=self.command, args=self.args, env=None)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            self._stack = stack
            logger.info("mcp_connected", server=self.name)
            return self
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(
                f"MCP Sunucusuna Bağlnamadı ({self.name}): {exc}"
            ) from exc

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None
        logger.info("mcp_disconnected", server=self.name)

    async def list_tools(self) -> list[Tool]:
        if self._session is None:
            raise MCPConnectionError("Session açık değil - 'async with' içiinde kullan")
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Tool'u çağırır ve QueryResult payload'ını dict olarak döner.
            Args:
                name: Tool adı (`query_sql`, `list_tables`, ...).
                arguments: Tool argümanları.
            Returns:
                QueryResult şeklindeki dict (server'ın döndürdüğü JSON).
            Raises:
                MCPConnectionError: Session yok, timeout, iletişim veya parse hatası.
        """
        if self._session is None:
            raise MCPConnectionError("SEssion açık değil - 'async with' içinde kullan")
        try:
            call = self._session.call_tool(name, arguments)
            result = await asyncio.wait_for(call, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            logger.error("mcp_call_timeout", tool=name, timeout=self.timeout)
            raise MCPConnectionError(f"Tool timeout ({name}, {self.timeout}s)") from exc
        except Exception as exc:
            logger.exception("mcp_call_failed", tool=name)
            raise MCPConnectionError(f"Tool çağrısı başarısız ({name}): {exc}") from exc

        # MCP tool sonucu content[0].text içinde JSON string olarak gelir
        content = result.content
        if not content:
            raise MCPConnectionError(f"Tool ({name}) boş yanıt döndü")
        first = content[0]
        text = getattr(first, "text", None)
        if text is None:
            raise MCPConnectionError(
                f"Tool ({name}) beklenmeyen içerik tipi: {type(first).__name__}"
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPConnectionError(f"Tool ({name}) yanıtı JSON parse edilemedi") from exc

def duckdb_client() -> MCPClient:
    """Settings'ten DuckDB MCP client'ı üretir."""
    s=get_settings()
    return MCPClient(
        name="duckdb",
        command=s.mcp_duckdb_server_cmd,
        args=s.mcp_duckdb_server_args,
        timeout=s.mcp_tool_timeout
    )



