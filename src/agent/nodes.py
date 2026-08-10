"""
LangGraph agent node functions.

Her node saf async fonksiyon: AgentState alır, güncellenmiş kısmi state döner.
Beş node:
    schema_discovery    → list_tables + describe_table topla, schema_context üret
    sql_generation      → question + schema → SQL (ilk deneme)
    mcp_tool_execution  → SQL çalıştır; başarı result, başarısızlık last_error*
    error_analysis      → hata payload'u → düzeltilmiş SQL (retry)
    summarize           → başarılı result → doğal dilde cevap
"""

from __future__ import annotations

from typing import Any, Callable, Literal

import anyio
from langgraph.types import interrupt

from src.agent.llm import SQLOutput, get_sql_llm, get_summary_llm
from src.agent.prompts import (
    build_error_analysis_prompt,
    build_sql_generation_prompt,
    build_summarize_prompt,
    format_schema_context,
)
from src.agent.state import AgentState
from src.clients.mcp_client import (
    MCPClient,
    MCPHttpClient,
    duckdb_client,
    duckdb_http_client,
    postgres_client,
    postgres_http_client,
)
from src.config import get_settings
from src.core.logging import get_logger
from src.core.semantic_layer import build_semantic_context
from src.mcp_servers.schemas import QueryResult

log = get_logger(__name__, component="agent_nodes")

Source = Literal["duckdb", "postgres"]

_STDIO_FACTORIES: dict[Source, Any] = {"duckdb": duckdb_client, "postgres": postgres_client}
_HTTP_FACTORIES: dict[Source, Any] = {"duckdb": duckdb_http_client, "postgres": postgres_http_client}


def _source_factories() -> dict[Source, Any]:
    """
    MCP_TRANSPORT config'ine göre doğru client factory sözlüğünü döner.

    Eski durum: sadece stdio fabrikaları vardı, dict sabit ve modül seviyesindeydi.
    Yeni durum: MCP_TRANSPORT=stdio (default) → subprocess başlatır;
                MCP_TRANSPORT=http → stateless HTTP endpointine bağlanır.
    Config cache'li olduğundan bu çağrı her seferinde disk okumaz.
    """
    return _HTTP_FACTORIES if get_settings().mcp_transport == "http" else _STDIO_FACTORIES


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
    timeout = get_settings().query_timeout_seconds
    schemas: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for source, factory in _source_factories().items():
        try:
            with anyio.fail_after(timeout):
                schemas[source] = await _collect_source_schema(source, factory)
        except TimeoutError:
            log.warning("node_timeout", node="schema_discovery", source=source, timeout_s=timeout)
        except Exception as exc:
            log.warning("source_unavailable", source=source, error=str(exc)[:200])

    schema_context = format_schema_context(schemas)

    semantic = build_semantic_context(schemas, get_settings().data_dir)
    if semantic:
        schema_context = schema_context + "\n\n" + semantic

    log.info(
        "schema_discovered",
        sources=list(schemas.keys()),
        chars=len(schema_context),
    )
    return {"schema_context": schema_context}


# Node 1.5: sql_validation (dry-run)
async def sql_validation(state: AgentState) -> AgentState:
    """
    EXPLAIN ile SQL'i veri çekmeden doğrular.
    Hata varsa last_error doldurur — graph error_analysis'e yönlendirir.
    Başarıysa boş dict döner, akış devam eder.
    """
    source: Source = state.get("source", "duckdb")
    factory = _source_factories()[source]
    log.info("node_start", node="sql_validation", source=source)

    try:
        async with factory() as client:
            raw = await client.call_tool("explain_sql", {"sql": state["sql"]})
        result = QueryResult(**raw)
        if result.status == "success":
            log.info("validation_ok", source=source)
            return {}
        log.warning("validation_failed", source=source, message=(result.message or "")[:200])
        return {"last_error": result.message or "", "last_error_type": result.error_type or "ValidationError"}
    except Exception as exc:
        # explain_sql tool yoksa (postgres gibi) veya beklenmedik hata → geç, çalıştırsın
        log.warning("validation_skipped", source=source, reason=str(exc)[:120])
        return {}


# Node 1.75: human_approval
async def human_approval(state: AgentState) -> AgentState:
    """
    Tahmini satır sayısı APPROVAL_ROW_THRESHOLD'u aşarsa kullanıcı onayı ister.
    Onay gelmezse final_answer set eder — graph END'e gider.
    Threshold 0 ise devre dışı.
    """
    threshold = get_settings().approval_row_threshold
    if threshold <= 0:
        return {}

    source: Source = state.get("source", "duckdb")
    factory = _source_factories()[source]
    log.info("node_start", node="human_approval", source=source)

    estimated: int = 0
    try:
        count_sql = f"SELECT COUNT(*) AS __cnt FROM ({state['sql']}) __approval_sub"
        async with factory() as client:
            raw = await client.call_tool("query_sql", {"sql": count_sql})
        result = QueryResult(**raw)
        if result.status == "success" and result.data:
            estimated = int(result.data[0].get("__cnt", 0))
    except Exception as exc:
        log.warning("approval_check_failed", reason=str(exc)[:120])
        return {}

    # interrupt() try dışında — NodeInterrupt exception yakalanmasın
    if estimated > threshold:
        log.warning("approval_required", estimated_rows=estimated, threshold=threshold)
        decision: str = interrupt({
            "estimated_rows": estimated,
            "threshold": threshold,
            "sql": state["sql"],
            "source": source,
        })
        if str(decision).strip().lower() not in ("e", "evet", "y", "yes"):
            log.info("approval_denied")
            return {"final_answer": f"Sorgu iptal edildi (tahmini {estimated:,} satır, eşik {threshold:,})."}
        log.info("approval_granted", estimated_rows=estimated)

    return {}


# Node 2: sql_generation
async def sql_generation(state: AgentState) -> AgentState:
    """
    Kullanıcının doğal dildeki sorusunu ve schema_context bilgisini alarak LLM'e ilk SQL sorgusunu ürettirir.
    get_sql_llm() structured output döndürür: SQLOutput(source, sql, rationale).
    Regex veya string parse işlemi yok — Pydantic şeması garantiler.
    """
    attempt = state.get("attempt", 0) + 1
    log.info("node_start", node="sql_generation", attempt=attempt)

    messages = build_sql_generation_prompt(
        question=state["question"],
        schema_context=state["schema_context"],
    )
    output: SQLOutput = await get_sql_llm().ainvoke(messages)
    log.info("sql_generated", attempt=attempt, source=output.source, rationale=output.rationale, sql_preview=output.sql[:120])
    return {"sql": output.sql, "source": output.source, "attempt": attempt}


# Node 3: mcp_tool_execution
async def mcp_tool_execution(state: AgentState) -> AgentState:
    """
    state["sql"] sorgusunu, belirlenen state["source"] üzerindeki MCP sunucusunda (query_sql tool'u ile) çalıştırır.
    Sorgu Başarılıysa: QueryResult nesnesini result anahtarına koyar.
    Sorgu Hatalıysa: Çökmez! last_error ve last_error_type alanlarını doldurarak hatayı self-correction döngüsüne teslim eder.,
    """

    source: Source = state.get("source", "duckdb")
    factory = _source_factories()[source]
    timeout = get_settings().query_timeout_seconds
    log.info("node_start", node="mcp_tool_execution", source=source)
    raw: dict[str, Any] | None = None
    try:
        with anyio.fail_after(timeout):
            async with factory() as client:
                raw = await client.call_tool("query_sql", {"sql": state["sql"]})
    except TimeoutError:
        log.error("node_timeout", node="mcp_tool_execution", timeout_s=timeout)
        return {
            "last_error": f"Sorgu {timeout:.0f}s içinde tamamlanamadı (timeout).",
            "last_error_type": "TimeoutError",
        }
    if raw is None:
        return {"last_error": "Sorgu sonucu alınamadı.", "last_error_type": "EmptyResult"}

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
    sql_generation gibi structured output kullanır; düzeltilmiş sql + yeni source + rationale döner.
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
    output: SQLOutput = await get_sql_llm().ainvoke(messages)
    log.info("sql_regenerated", attempt=attempt, source=output.source, rationale=output.rationale, sql_preview=output.sql[:120])
    return {"sql": output.sql, "source": output.source, "attempt": attempt}


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
    response = await get_summary_llm().ainvoke(messages)
    final = (
        response.content if isinstance(response.content, str) else str(response.content)
    ).strip()
    log.info("summary_generated", chars=len(final))
    return {"final_answer": final}
