"""
LangGraph agent node functions.

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
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from src.agent.prompts import (
    build_error_analysis_prompt,
    build_sql_generation_prompt,
    build_summarize_prompt,
    format_schema_context,
)
from src.agent.state import AgentState
from src.clients.mcp_client import MCPClient, duckdb_client, postgres_client
from src.config import get_settings
from src.core.logging import get_logger
from src.mcp_servers.schemas import QueryResult

log = get_logger(__name__, component="agent_nodes")

#DOTALL, MULTİLİNE:LLM çıktısındaki SQL bloklarını ve -- SOURCE: etiketlerini satır bağımsız yakalamak için kullanılan regex derleyicileridir.
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SOURCE_HINT_RE = re.compile(r"^\s*--\s*SOURCE\s*:\s*(\w+)\s*$", re.IGNORECASE | re.MULTILINE)

Source = Literal["duckdb", "postgres"]
_SOURCE_FACTORIES: dict[Source, Any] = {
    "duckdb": duckdb_client,
    "postgres": postgres_client,
}


# LLM factory
@lru_cache(maxsize=1) # Süreç boyunca tek bir ChatOpenAI nesnesi üretilerek hafızada tutulur.
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
    match = _FENCE_RE.search(text) # regex'i (re.compile) ile kod çitlerini yakalar ve temizler.
    if match:
        text = match.group(1).strip()
    return text.rstrip(";").strip()


def _parse_source_and_sql(raw: Any) -> tuple[Source, str]:
    """
    LLM çıktısından '-- SOURCE: xxx' etiketini ve altındaki SQL'i ayıklar.
    Etiket yoksa varsayılan olarak 'duckdb' döner (geriye uyumlu).
    """
    text = _clean_sql(raw)
    match = _SOURCE_HINT_RE.search(text)
    if not match:
        return "duckdb", text
    value = match.group(1).strip().lower()
    source: Source = "postgres" if value in {"postgres", "pg", "postgresql"} else "duckdb"
    # SOURCE satırını SQL'den çıkar
    sql_only = _SOURCE_HINT_RE.sub("", text, count=1).strip()
    return source, sql_only


async def _collect_source_schema(source: Source, client_factory: Any) -> dict[str, list[dict[str, Any]]]:
    """
    Tek bir MCP kaynağından tüm tabloların şemasını çeker.Verilen bir MCP sunucusuna asenkron bağlanarak (async with client_factory()) mevcut tüm tabloları ve bu tabloların kolon şemalarını çeker.
    """
    client: MCPClient
    async with client_factory() as client:
        tables_resp = await client.call_tool("list_tables", {})
        table_names = [row["table"] for row in tables_resp["data"]]
        tables_map: dict[str, list[dict[str, Any]]] = {}
        for name in table_names:
            resp = await client.call_tool("describe_table", {"table": name})
            tables_map[name] = resp["data"]
    log.info("source_schema_ok", source=source, tables=len(table_names))
    return tables_map


# Node 1: schema_discovery
async def schema_discovery(state: AgentState) -> AgentState:
    """
    Her iki MCP kaynağını da sorgular; bir kaynak düşse diğeriyle devam eder.
    LLM'in veritabanında hangi tabloların ve kolonların olduğunu bilmesi için gerekli olan şema bağlamını hazırlar.
    """
    log.info("node_start", node="schema_discovery")
    schemas: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for source, factory in _SOURCE_FACTORIES.items():
        try:
            schemas[source] = await _collect_source_schema(source, factory)
        except Exception as exc:
            log.warning("source_unavailable", source=source, error=str(exc)[:200])

    schema_context = format_schema_context(schemas)
    log.info(
        "schema_discovered",
        sources=list(schemas.keys()),
        chars=len(schema_context),
    )
    return {"schema_context": schema_context}


# Node 2: sql_generation
async def sql_generation(state: AgentState) -> AgentState:
    """
    Kullanıcının doğal dildeki sorusunu ve schema_context bilgisini alarak LLM'e ilk SQL sorgusunu ürettirir.
    Deneme sayısını 1 yapar (attempt = 1), üretilen SQL'i ve seçilen veri kaynağını (source) state'e yazar.
    """
    attempt = state.get("attempt", 0) + 1
    log.info("node_start", node="sql_generation", attempt=attempt)

    messages = build_sql_generation_prompt(
        question=state["question"],
        schema_context=state["schema_context"],
    )
    response = await _llm().ainvoke(messages)
    source, sql = _parse_source_and_sql(response.content)
    log.info("sql_generated", attempt=attempt, source=source, sql_preview=sql[:120])
    return {"sql": sql, "source": source, "attempt": attempt}


# Node 3: mcp_tool_execution
async def mcp_tool_execution(state: AgentState) -> AgentState:
    """
    state["sql"] sorgusunu, belirlenen state["source"] üzerindeki MCP sunucusunda (query_sql tool'u ile) çalıştırır.
    Sorgu Başarılıysa: QueryResult nesnesini result anahtarına koyar.
    Sorgu Hatalıysa: Çökmez! last_error ve last_error_type alanlarını doldurarak hatayı self-correction döngüsüne teslim eder.,
    """

    source: Source = state.get("source", "duckdb")
    factory = _SOURCE_FACTORIES[source]
    log.info("node_start", node="mcp_tool_execution", source=source)
    async with factory() as client:
        raw = await client.call_tool("query_sql", {"sql": state["sql"]})

    result = QueryResult(**raw)
    if result.status == "success":
        log.info(
            "execution_ok",
            source=source,
            rows=result.row_count,
            elapsed_ms=result.elapsed_ms,
        )
        return {"result": result}

    log.warning(
        "execution_failed",
        source=source,
        error_type=result.error_type,
        message=(result.message or "")[:200],
    )
    return {
        "last_error": result.message or "",
        "last_error_type": result.error_type or "UnknownError",
    }


# Node 4: error_analysis
async def error_analysis(state: AgentState) -> AgentState:
    """
    Patlayan SQL sorgusunu, veritabanının döndürdüğü ham hata mesajını ve hata tipini alıp LLM'e sunar ve düzeltilmiş yeni bir SQL ürettirir.
    Projedeki Rolü: Self-correction (kendi kendini düzeltme) mekanizmasının karar düğümüdür. attempt sayısını 1 artırır.
    """
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
    source, sql = _parse_source_and_sql(response.content)
    log.info("sql_regenerated", attempt=attempt, source=source, sql_preview=sql[:120])
    return {"sql": sql, "source": source, "attempt": attempt}


# Node 5: summarize
async def summarize(state: AgentState) -> AgentState:
    """Veritabanından başarıyla dönen ham QueryResult verisini (LLM prompt'unu şişirmemek için ilk 20 satırı) alır ve SUMMARIZE_SYSTEM talimatıyla Türkçe, anlaşılır bir iş dili özetine çevirir.."""
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
    final = (
        response.content if isinstance(response.content, str) else str(response.content)
    ).strip()
    log.info("summary_generated", chars=len(final))
    return {"final_answer": final}
