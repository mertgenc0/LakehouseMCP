"""Integration tests for src/agent/graph.py — LLM MOCK'lanır.

Gerçek OpenAI çağrısı YAPILMAZ. get_sql_llm / get_summary_llm patch ile
SQLOutput döndüren sahte nesnelerle değiştirilir; self-correction loop
deterministik doğrulanır.

DuckDB tarafı gerçek — data/processed/ altında seed parquet'leri olmalı.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.graph import build_graph, run
from src.agent.llm import SQLOutput


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """LangChain AIMessage benzeri; sadece .content lazım (summarize node için)."""

    def __init__(self, content: str) -> None:
        self.content = content


class FakeSQLLLM:
    """
    get_sql_llm() yerine geçen stub — SQLOutput nesnelerini sırayla döner.

    Eski yaklaşım: _llm() string döndürürdü → regex ile ayrıştırılırdı.
    Yeni yaklaşım: structured output; stub da doğrudan SQLOutput döndürür.
    """

    def __init__(self, outputs: list[SQLOutput]) -> None:
        self._outputs = list(outputs)
        self._i = 0

    async def ainvoke(self, _messages: object) -> SQLOutput:
        if self._i >= len(self._outputs):
            raise RuntimeError(
                f"FakeSQLLLM tükendi (istenen: {self._i + 1}, verilen: {len(self._outputs)})"
            )
        out = self._outputs[self._i]
        self._i += 1
        return out


class FakeSummaryLLM:
    """get_summary_llm() yerine geçen stub — sabit FakeResponse döner."""

    async def ainvoke(self, _messages: object) -> FakeResponse:
        return FakeResponse("Toplam sipariş sayısı hesaplanmıştır.")


def _sql_out(sql: str, source: str = "duckdb") -> SQLOutput:
    """Test SQLOutput nesnesi oluşturmak için kısayol."""
    return SQLOutput(source=source, sql=sql, rationale="test")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_graph_compiles(self):
        g = build_graph()
        assert g is not None

    def test_graph_has_expected_nodes(self):
        g = build_graph()
        node_ids = set(getattr(g, "nodes", {}).keys()) if hasattr(g, "nodes") else set()
        if not node_ids:
            pytest.skip("Graph node inspection bu langgraph versiyonunda desteklenmiyor")
        expected = {
            "schema_discovery",
            "sql_generation",
            "mcp_tool_execution",
            "error_analysis",
            "summarize",
            "give_up",
        }
        assert expected <= node_ids


# ---------------------------------------------------------------------------
# Behavioral tests — mocked LLM
# ---------------------------------------------------------------------------

class TestGraphBehavior:
    async def test_happy_path_no_retry(self):
        """LLM ilk seferde doğru SQL üretir → attempt=1, result var, cevap var."""
        sql_llm = FakeSQLLLM([_sql_out("SELECT COUNT(*) AS n FROM orders")])
        with (
            patch("src.agent.nodes.get_sql_llm", return_value=sql_llm),
            patch("src.agent.nodes.get_summary_llm", return_value=FakeSummaryLLM()),
        ):
            state = await run("Kaç sipariş var?")

        assert state["attempt"] == 1
        assert "result" in state
        assert state["result"].status == "success"
        assert "final_answer" in state
        assert len(state["final_answer"]) > 0

    async def test_self_correction_recovers_from_first_error(self):
        """İlk SQL bozuk → error_analysis → düzeltilmiş SQL çalışır → attempt=2."""
        sql_llm = FakeSQLLLM(
            [
                _sql_out("SELECT bogus_col FROM orders"),       # hatalı
                _sql_out("SELECT COUNT(*) AS n FROM orders"),   # düzeltme
            ]
        )
        with (
            patch("src.agent.nodes.get_sql_llm", return_value=sql_llm),
            patch("src.agent.nodes.get_summary_llm", return_value=FakeSummaryLLM()),
        ):
            state = await run("Kaç sipariş var?")

        assert state["attempt"] == 2, "İkinci denemede geçmeliydi"
        assert state["result"].status == "success"
        assert "final_answer" in state

    async def test_max_retries_triggers_give_up(self):
        """MAX_RETRIES aşılınca give_up node'u devreye girer, summarize çağrılmaz."""
        sql_llm = FakeSQLLLM(
            [
                _sql_out("SELECT bogus1 FROM orders"),
                _sql_out("SELECT bogus2 FROM orders"),
                _sql_out("SELECT bogus3 FROM orders"),
            ]
        )
        with (
            patch("src.agent.nodes.get_sql_llm", return_value=sql_llm),
            patch("src.agent.nodes.get_summary_llm", return_value=FakeSummaryLLM()),
        ):
            state = await run("Cevaplanamaz soru")

        assert state.get("attempt", 0) >= 3
        assert "final_answer" in state
        answer_lower = state["final_answer"].lower()
        assert "deneme" in answer_lower or "üzgünüm" in answer_lower
        assert state.get("result") is None

    async def test_structured_output_source_routing(self):
        """
        Eski testin yerini alan: structured output ile kaynak seçimi doğrudan
        SQLOutput.source alanından gelir, regex parsing gerekmez.
        """
        sql_llm = FakeSQLLLM([_sql_out("SELECT COUNT(*) AS n FROM orders", source="duckdb")])
        with (
            patch("src.agent.nodes.get_sql_llm", return_value=sql_llm),
            patch("src.agent.nodes.get_summary_llm", return_value=FakeSummaryLLM()),
        ):
            state = await run("Kaç sipariş var?")

        assert state["source"] == "duckdb"
        assert state["result"].status == "success"
        assert "```" not in state["sql"]