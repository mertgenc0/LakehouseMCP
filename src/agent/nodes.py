"""LangGraph agent node functions.

Her node saf async fonksiyon: AgentState alır, güncellenmiş kısmi state döner.
CLAUDE.md §5: node'lar tamamen bağımsız, I/O dışında global mutasyon yok.

Beş node:
    schema_discovery    → list_tables + describe_table topla, schema_context üret
    sql_generation      → question + schema → SQL (ilk deneme)
    mcp_tool_execution  → SQL çalıştır; başarı result, başarısızlık last_error*
    error_analysis      → hata payload'u → düzeltilmiş SQL (retry)
    summarize           → başarılı result → doğal dilde cevap
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from src.agent.prompts import (
    build_error_analysis_prompt,
    build_sql_generation_prompt,
    build_summarize_prompt,
    format_schema_context,
)
from src.agent.state import AgentState
from src.clients.mcp_client import duckdb_client
from src.config import get_settings
from src.core.logging import get_logger
from src.mcp_servers.schemas import QueryResult

log = get_logger(__name__, component="agent_nodes")

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)



# LLM factory
@lru_cache(maxsize=1)
def _llm() -> ChatOpenAI:
    """ChatOpenAI singleton — process başına tek instance."""
    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": s.openai_model,
        "temperature": s.openai_temperature,
        "max_tokens": s.openai_max_tokens,
        "timeout": s.openai_request_timeout,
        "api_key": s.openai_api_key.get_secret_value(),
    }
    if s.openai_base_url:
        kwargs["base_url"] = s.openai_base_url
    return ChatOpenAI(**kwargs)


def _clean_sql(raw: Any) -> str:
    """LLM çıktısındaki kod fence ve fazla boşlukları temizler."""
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    match = _FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    return text.rstrip(";").strip()



# Node 1: schema_discovery
async def schema_discovery(state: AgentState) -> AgentState:
    """MCP tool'ları ile şemayı topla, state["schema_context"]'i doldur."""
    log.info("node_start", node="schema_discovery")
    async with duckdb_client() as client:
        tables_resp = await client.call_tool("list_tables", {})
        table_names = [row["table"] for row in tables_resp["data"]]

        describes: dict[str, list[dict[str, Any]]] = {}
        for name in table_names:
            resp = await client.call_tool("describe_table", {"table": name})
            describes[name] = resp["data"]

    schema_context = format_schema_context(table_names, describes)
    log.info("schema_discovered", tables=table_names, chars=len(schema_context))
    return {"schema_context": schema_context}



# Node 2: sql_generation
async def sql_generation(state: AgentState) -> AgentState:
    """LLM ile ilk SQL'i üret. attempt sayacını 1'e ayarlar."""
    attempt = state.get("attempt", 0) + 1
    log.info("node_start", node="sql_generation", attempt=attempt)

    messages = build_sql_generation_prompt(
        question=state["question"],
        schema_context=state["schema_context"],
    )
    response = await _llm().ainvoke(messages)
    sql = _clean_sql(response.content)
    log.info("sql_generated", attempt=attempt, sql_preview=sql[:120])
    return {"sql": sql, "attempt": attempt}



# Node 3: mcp_tool_execution
async def mcp_tool_execution(state: AgentState) -> AgentState:
    """query_sql tool'unu çağır; başarıysa result, değilse last_error* güncelle."""
    log.info("node_start", node="mcp_tool_execution")
    async with duckdb_client() as client:
        raw = await client.call_tool("query_sql", {"sql": state["sql"]})

    result = QueryResult(**raw)
    if result.status == "success":
        log.info("execution_ok", rows=result.row_count, elapsed_ms=result.elapsed_ms)
        return {"result": result}

    log.warning(
        "execution_failed",
        error_type=result.error_type,
        message=(result.message or "")[:200],
    )
    return {
        "last_error": result.message or "",
        "last_error_type": result.error_type or "UnknownError",
    }



# Node 4: error_analysis
async def error_analysis(state: AgentState) -> AgentState:
    """Hatayı LLM'e ver, düzeltilmiş SQL üret. attempt sayacını artırır."""
    attempt = state.get("attempt", 0) + 1
    log.info("node_start", node="error_analysis", attempt=attempt)

    messages = build_error_analysis_prompt(
        question=state["question"],
        schema_context=state["schema_context"],
        failed_sql=state["sql"],
        error_type=state["last_error_type"],
        error_message=state["last_error"],
    )
    response = await _llm().ainvoke(messages)
    sql = _clean_sql(response.content)
    log.info("sql_regenerated", attempt=attempt, sql_preview=sql[:120])
    return {"sql": sql, "attempt": attempt}


# Node 5: summarize
async def summarize(state: AgentState) -> AgentState:
    """Başarılı sonucu iş diliyle özetle."""
    log.info("node_start", node="summarize")
    result = state["result"]
    # Prompt'u şişirmemek için ilk 20 satır yeter — row_count zaten payload'da
    payload = {
        "row_count": result.row_count,
        "columns": result.columns,
        "data": result.data[:20],
    }
    messages = build_summarize_prompt(question=state["question"], result=payload)
    response = await _llm().ainvoke(messages)
    final = (response.content if isinstance(response.content, str) else str(response.content)).strip()
    log.info("summary_generated", chars=len(final))
    return {"final_answer": final}