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
    sql_generation ─────────► mcp_tool_execution
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                success           retry             give_up
                    │                │                │
                    ▼                ▼                ▼
                summarize      error_analysis      give_up
                    │                │                │
                    │                └─► mcp_tool_execution (loop)
                    │                                 │
                    └───────────► END ◄───────────────┘
"""

from __future__ import annotations

from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    error_analysis,
    mcp_tool_execution,
    schema_discovery,
    sql_generation,
    summarize,
)
from src.agent.state import AgentState
from src.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__, component="agent_graph")


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


def _route_after_execution(state: AgentState) -> Literal["success", "retry", "give_up"]:
    """mcp_tool_execution sonrası yönlendirme — tek yer, tek kural sonuç var ise succes yoksa max retries geçene kadar devam değilse pes et."""
    if state.get("result") is not None:
        return "success"
    if state.get("attempt", 0) >= get_settings().max_retries:
        return "give_up"
    return "retry"


def build_graph() -> Any:
    """StateGraph'ı düğümlerle ve edge'lerle inşa edip compile eder."""
    # LangGraph StateGraph nesnesini oluşturur, düğümleri/çizgileri ekler ve çalıştırılabilir bir grafik haline getirip derler
    g = StateGraph(AgentState)

    g.add_node("schema_discovery", schema_discovery)
    g.add_node("sql_generation", sql_generation)
    g.add_node("mcp_tool_execution", mcp_tool_execution)
    g.add_node("error_analysis", error_analysis)
    g.add_node("summarize", summarize)
    g.add_node("give_up", give_up)

    g.add_edge(START, "schema_discovery")
    g.add_edge("schema_discovery", "sql_generation")
    g.add_edge("sql_generation", "mcp_tool_execution")

    g.add_conditional_edges(
        "mcp_tool_execution",
        _route_after_execution,
        {
            "success": "summarize",
            "retry": "error_analysis",
            "give_up": "give_up",
        },
    )

    g.add_edge("error_analysis", "mcp_tool_execution")
    g.add_edge("summarize", END)
    g.add_edge("give_up", END)

    return g.compile()


async def run(question: str) -> AgentState:
    """
     Dış dünyadan (örneğin main.py CLI arayüzünden) gelen soruyu alır, başlangıç durumunu (initial = {"question": question}) hazırlar, derlenmiş grafiği
    asenkron olarak çalıştırır (graph.ainvoke(initial)) ve oluşan son state'i döndürür.

     Syntax/Semantik: cast(AgentState, final_state) ifadesi kullanılır. ainvoke geriye genel bir dict döndüğü için tip denetleyicisi mypy'ye bu çıktının
    AgentState tipinde olduğunu bildirir.
    """
    graph = build_graph()
    initial: AgentState = {"question": question}
    log.info("run_start", question=question[:120])
    final_state = await graph.ainvoke(initial)
    log.info(
        "run_end",
        has_answer="final_answer" in final_state,
        attempts=final_state.get("attempt", 0),
    )
    # cast: ainvoke Any döner; state şeması AgentState TypedDict ile garanti edildi.
    return cast(AgentState, final_state)
