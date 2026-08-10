##--Projemizin Orkestrasyon ve Akış Kontrol (Orchestration & Graph Execution) Katmanı’dır--##
"""
LangGraph StateGraph derlemesi + self-correction routing.
Akış:

    START
      │
      ▼
    schema_discovery
      │
      ▼
    sql_generation
      │
      ▼
    sql_validation ──(invalid)──► error_analysis ──► mcp_tool_execution
      │                                                      │
    (valid)                            ┌─────────────────────┤
      ▼                                │                     │
    human_approval ──► mcp_tool_execution          retry / give_up
      │                      │
   (denied)               (success)
      │                      │
      ▼                      ▼
     END                 summarize ──► END
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Coroutine, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agent.nodes import (
    error_analysis,
    human_approval,
    mcp_tool_execution,
    schema_discovery,
    sql_generation,
    sql_validation,
    summarize,
)
from src.agent.state import AgentState
from src.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__, component="agent_graph")

# Callback tipi: interrupt payload alır, kullanıcı cevabını string döner
ApprovalCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, str]]


async def give_up(state: AgentState) -> AgentState:
    """MAX_RETRIES aşıldı — kullanıcıya açıklayıcı bir başarısızlık mesajı."""
    attempts = state.get("attempt", 0)
    err = state.get("last_error", "bilinmeyen hata")
    err_type = state.get("last_error_type", "UnknownError")
    log.error("max_retries_exceeded", attempts=attempts, error_type=err_type)
    return {
        "final_answer": (
            f"Üzgünüm, {attempts} deneme sonunda sorunuz için doğru SQL üretemedim.\n"
            f"Son hata: [{err_type}] {err}\n"
            "Soruyu farklı şekilde ifade etmeyi deneyebilirsiniz."
        ),
    }


def _route_after_validation(state: AgentState) -> Literal["human_approval", "error_analysis"]:
    """sql_validation sonrası: hata varsa error_analysis, yoksa onay kapısına geç."""
    if state.get("last_error"):
        return "error_analysis"
    return "human_approval"


def _route_after_approval(state: AgentState) -> Literal["mcp_tool_execution", "end"]:
    """human_approval sonrası: iptal edildiyse END, aksi hâlde çalıştır."""
    if state.get("final_answer"):
        return "end"
    return "mcp_tool_execution"


def _route_after_execution(state: AgentState) -> Literal["success", "retry", "give_up"]:
    """mcp_tool_execution sonrası yönlendirme."""
    if state.get("result") is not None:
        return "success"
    if state.get("attempt", 0) >= get_settings().max_retries:
        return "give_up"
    return "retry"


def build_graph(checkpointer: MemorySaver | None = None) -> Any:
    """StateGraph’ı düğümlerle ve edge’lerle inşa edip compile eder."""
    g = StateGraph(AgentState)

    g.add_node("schema_discovery", schema_discovery)
    g.add_node("sql_generation", sql_generation)
    g.add_node("sql_validation", sql_validation)
    g.add_node("human_approval", human_approval)
    g.add_node("mcp_tool_execution", mcp_tool_execution)
    g.add_node("error_analysis", error_analysis)
    g.add_node("summarize", summarize)
    g.add_node("give_up", give_up)

    g.add_edge(START, "schema_discovery")
    g.add_edge("schema_discovery", "sql_generation")
    g.add_edge("sql_generation", "sql_validation")

    g.add_conditional_edges(
        "sql_validation",
        _route_after_validation,
        {"human_approval": "human_approval", "error_analysis": "error_analysis"},
    )

    g.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"mcp_tool_execution": "mcp_tool_execution", "end": END},
    )

    g.add_conditional_edges(
        "mcp_tool_execution",
        _route_after_execution,
        {"success": "summarize", "retry": "error_analysis", "give_up": "give_up"},
    )

    # Retry sonrası validation atla — zaten bir kez geçti
    g.add_edge("error_analysis", "mcp_tool_execution")
    g.add_edge("summarize", END)
    g.add_edge("give_up", END)

    return g.compile(checkpointer=checkpointer)


async def run(
    question: str,
    approval_callback: ApprovalCallback | None = None,
) -> AgentState:
    """
    Soruyu agent’a gönderir ve son state’i döner.

    approval_callback: human_approval interrupt tetiklenirse çağrılır.
        Parametre olarak interrupt payload dict’i alır, kullanıcının kararını
        string döner ("e"/"y" = onay, diğer = iptal).
        None ise interrupt otomatik onaylanır (eval/test uyumluluğu).
    """
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    thread_id = str(uuid.uuid4())

    config = RunnableConfig(
        configurable={"thread_id": thread_id},
        run_name="lakehouse-copilot",
        tags=["lakehouse-copilot"],
        metadata={"question": question[:200]},
    )

    initial: AgentState = {"question": question}
    log.info("run_start", question=question[:120])

    state = await graph.ainvoke(initial, config=config)

    # human_approval interrupt’ı varsa kullanıcıya sor ve devam et
    while state.get("__interrupt__"):
        interrupt_payload: dict[str, Any] = state["__interrupt__"][0].value
        log.info("graph_interrupted", payload=interrupt_payload)

        if approval_callback is not None:
            decision = await approval_callback(interrupt_payload)
        else:
            decision = "y"  # eval/test modunda otomatik onayla

        state = await graph.ainvoke(Command(resume=decision), config=config)

    log.info(
        "run_end",
        has_answer="final_answer" in state,
        attempts=state.get("attempt", 0),
    )
    return cast(AgentState, state)
