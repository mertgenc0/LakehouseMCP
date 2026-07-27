##--MCP tool'larını LangChain Tool nesnelerine sarar; böylece LLM function-calling ile "hangi tool'u ne parametrelerle çağıracağım" kararını verebilir.--##

"""
LangChain Tool adapter'ları — MCP tool'larını LLM'in görebileceği forma çevirir.
Agent bu tool'ları LLM'e bind eder; LLM function-calling ile hangisini çağıracağına
karar verir. Bu dosyanın altında MCP protokolü, üstünde LangGraph agent çalışır.

Neden gerekli?
  - LLM MCP protokolünü doğrudan konuşamaz. OpenAI/Anthropic modelleri function-calling interface'i bilir: "sana bu fonksiyonları veriyorum, hangisini çağıracaksın?" formatı.
  - LangChain'in StructuredTool sınıfı bu interface'in kanonik implementasyonu — pydantic argüman şeması + description + async callable.
  - Bu adaptör olmadan LLM tool'ları göremez → sadece freeform metin döner → ajan agent olamaz.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.clients.mcp_client import duckdb_client
from src.core.logging import get_logger

log = get_logger(__name__, component="agent_tools")


# Input schemas — LLM function-calling parametrelerini bunlardan üretir
class QuerySqlInput(BaseModel):
    sql: str = Field(
        ...,
        description=(
            "SELECT veya WITH ile başlayan tek statement SQL. "
            "Guardrail otomatik LIMIT ekler, DDL/DML reddedilir."
        ),
    )


class DescribeTableInput(BaseModel):
    table: str = Field(..., description="list_tables çıktısındaki tablo adı.")


# Async runner'lar — her tool çağrısı için MCP session
async def _call(tool: str, arguments: dict[str, Any]) -> str:
    """MCP tool çağrısı yapar, sonucu LLM için JSON string olarak döner."""
    async with duckdb_client() as client:
        result = await client.call_tool(tool, arguments)
    log.info("tool_called", tool=tool, status=result.get("status"))
    return json.dumps(result, ensure_ascii=False, default=str)


async def _list_tables() -> str:
    return await _call("list_tables", {})


async def _describe_table(table: str) -> str:
    return await _call("describe_table", {"table": table})


async def _query_sql(sql: str) -> str:
    return await _call("query_sql", {"sql": sql})


# LangChain Tool nesneleri — LLM'e bind edilir
list_tables_tool = StructuredTool.from_function(
    coroutine=_list_tables,
    name="list_tables",
    description=(
        "DuckDB veri katmanındaki mevcut tabloları listeler. "
        "Her sorgudan ÖNCE hangi tabloların var olduğunu görmek için çağır."
    ),
)

describe_table_tool = StructuredTool.from_function(
    coroutine=_describe_table,
    name="describe_table",
    description=(
        "Verilen tablonun kolon adlarını, tiplerini ve nullable bilgisini döner. "
        "SQL yazmadan ÖNCE hangi kolonlar var öğrenmek için çağır."
    ),
    args_schema=DescribeTableInput,
)

query_sql_tool = StructuredTool.from_function(
    coroutine=_query_sql,
    name="query_sql",
    description=(
        "DuckDB üzerinde read-only SQL sorgusu çalıştırır. "
        "SELECT veya WITH ile başlamalı; DDL/DML yasak. "
        "Yanıt JSON zarfı: {status, row_count, columns, data, error_type, message}. "
        "status='error' ise error_type ve message'a bak, SQL'i düzeltip tekrar çağır."
    ),
    args_schema=QuerySqlInput,
)

ALL_TOOLS = [list_tables_tool, describe_table_tool, query_sql_tool]
