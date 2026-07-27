"""
Happy path: gerçek DuckDB subprocess ile session lifecycle.
Error paths: session-not-open, unreachable command.

"""
from __future__ import annotations

import pytest

from src.clients.mcp_client import MCPClient, duckdb_client
from src.core.exceptions import MCPConnectionError



# Happy path — gerçek subprocess
class TestDuckDBClientLifecycle:
    async def test_context_manager_connects_and_lists_tools(self):
        async with duckdb_client() as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert {"list_tables", "describe_table", "query_sql"} <= names

    async def test_call_tool_returns_query_result_dict(self):
        async with duckdb_client() as client:
            result = await client.call_tool("list_tables", {})
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert "data" in result

    async def test_call_tool_error_envelope_still_dict(self):
        async with duckdb_client() as client:
            result = await client.call_tool("query_sql", {"sql": "DELETE FROM t"})
        # Server tarafında guardrail bloklar; MCPConnectionError fırlatmaz — QueryResult error döner
        assert result["status"] == "error"
        assert result["error_type"] == "GuardrailViolation"



# Error paths — mock/faulty setup
class TestClientErrorGuards:
    async def test_call_tool_without_session_raises(self):
        # __aenter__ çağrılmadan call_tool → session None
        client = MCPClient("test", "python", ["-c", "pass"], timeout=5)
        with pytest.raises(MCPConnectionError, match=r"[Ss]ession"):
            await client.call_tool("anything", {})

    async def test_list_tools_without_session_raises(self):
        client = MCPClient("test", "python", ["-c", "pass"], timeout=5)
        with pytest.raises(MCPConnectionError, match=r"[Ss]ession"):
            await client.list_tools()

    async def test_unreachable_command_raises_connection_error(self):
        # Var olmayan binary → subprocess başlatılamaz → MCPConnectionError
        client = MCPClient(
            "broken",
            "/definitely/not/a/real/binary_xyz",
            [],
            timeout=5,
        )
        with pytest.raises(MCPConnectionError):
            async with client:
                pass  # pragma: no cover — buraya gelmemeli

    async def test_bad_stdio_child_raises_connection_error(self):
        # Hemen exit eden bir süreç → MCP handshake başarısız
        client = MCPClient("dead", "python", ["-c", "import sys; sys.exit(0)"], timeout=5)
        with pytest.raises(MCPConnectionError):
            async with client:
                pass  # pragma: no cover