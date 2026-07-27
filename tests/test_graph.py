"""Integration tests for src/agent/graph.py — LLM MOCK'lanır.

Gerçek OpenAI çağrısı YAPILMAZ. `_llm()` factory'si patch ile fake bir sınıfla
değiştirilir; böylece self-correction loop deterministik doğrulanır.

DuckDB tarafı gerçek — data/processed/ altında seed parquet'leri olmalı.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.graph import build_graph, run



# Test helpers
class FakeResponse:
    """LangChain AIMessage benzeri; sadece .content lazım."""

    def __init__(self, content: str) -> None:
        self.content = content


class SequentialLLM:
    """LLM stub — verilen response listesini sırayla döner."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._i = 0

    async def ainvoke(self, _messages: object) -> FakeResponse:
        if self._i >= len(self._responses):
            raise RuntimeError(
                f"SequentialLLM tükendi (istenen: {self._i + 1}, verilen: {len(self._responses)})"
            )
        text = self._responses[self._i]
        self._i += 1
        return FakeResponse(text)



# Structural tests
class TestGraphStructure:
    def test_graph_compiles(self):
        g = build_graph()
        assert g is not None

    def test_graph_has_expected_nodes(self):
        g = build_graph()
        # CompiledStateGraph internal API — versiyona göre değişebilir; yumuşak assert
        node_ids = set(getattr(g, "nodes", {}).keys()) if hasattr(g, "nodes") else set()
        # nodes attribute yoksa test skip
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



# Behavioral tests — mocked LLM
class TestGraphBehavior:
    async def test_happy_path_no_retry(self):
        """LLM ilk seferde doğru SQL üretir → attempt=1, result var, cevap var."""
        fake = SequentialLLM(
            [
                "SELECT COUNT(*) AS n FROM orders",  # sql_generation
                "Toplam 1000 sipariş bulunmaktadır.",  # summarize
            ]
        )
        with patch("src.agent.nodes._llm", return_value=fake):
            state = await run("Kaç sipariş var?")

        assert state["attempt"] == 1
        assert "result" in state
        assert state["result"].status == "success"
        assert "final_answer" in state
        assert len(state["final_answer"]) > 0

    async def test_self_correction_recovers_from_first_error(self):
        """İlk SQL bozuk → error_analysis → düzeltilmiş SQL çalışır → attempt=2."""
        fake = SequentialLLM(
            [
                "SELECT bogus_col FROM orders",  # hatalı — kolon yok
                "SELECT COUNT(*) AS n FROM orders",  # düzeltme
                "Cevap: 1000 sipariş",  # summarize
            ]
        )
        with patch("src.agent.nodes._llm", return_value=fake):
            state = await run("Kaç sipariş var?")

        assert state["attempt"] == 2, "İkinci denemede geçmeliydi"
        assert state["result"].status == "success"
        assert "final_answer" in state

    async def test_max_retries_triggers_give_up(self):
        """MAX_RETRIES aşılınca give_up node'u devreye girer, final_answer başarısızlık mesajı."""
        # 3 hatalı SQL — hiçbiri geçmeyecek, summarize çağrılmayacak
        fake = SequentialLLM(
            [
                "SELECT bogus1 FROM orders",
                "SELECT bogus2 FROM orders",
                "SELECT bogus3 FROM orders",
            ]
        )
        with patch("src.agent.nodes._llm", return_value=fake):
            state = await run("Cevaplanamaz soru")

        # 3 deneme yapıldı, give_up tetiklendi
        assert state.get("attempt", 0) >= 3
        assert "final_answer" in state
        # give_up mesajı içinde "deneme" veya "Üzgünüm" geçmeli
        answer_lower = state["final_answer"].lower()
        assert "deneme" in answer_lower or "üzgünüm" in answer_lower
        # başarılı result olmamalı
        assert state.get("result") is None

    async def test_llm_output_with_code_fence_is_cleaned(self):
        """LLM markdown fence koyarsa _clean_sql ayıklar, SQL yine çalışır."""
        fake = SequentialLLM(
            [
                "```sql\nSELECT COUNT(*) AS n FROM orders\n```",
                "1000 kayıt var.",
            ]
        )
        with patch("src.agent.nodes._llm", return_value=fake):
            state = await run("Kaç?")

        assert state["result"].status == "success"
        assert "```" not in state["sql"]
