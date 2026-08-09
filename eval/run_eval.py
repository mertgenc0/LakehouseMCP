"""
eval/run_eval.py — Text-to-SQL execution accuracy değerlendirmesi.

Nasıl çalışır (hibrit mod):
  1. golden_set.jsonl'deki her soru agent'a gönderilir.
  2. Agent'ın ürettiği SQL çalıştırılır → sonuç A.
  3. expected_sql doğrudan DuckDB'de çalıştırılır → sonuç B (ground truth).
  4a. Exact match: A == B ise correct=True, method="exact"  (hızlı, ücretsiz)
  4b. LLM judge:  Exact match başarısızsa gpt-4o-mini'ye "bu cevap doğru mu?" diye
      sorulur → method="llm_judge"  (yalnızca başarısızlarda tetiklenir)
  5. Accuracy, retry, latency, method metrikleri raporlanır → eval/report.md.

Kullanım:
  python eval/run_eval.py --live                   # Gerçek LLM (API key gerekli)
  python eval/run_eval.py --live --questions q01,q03
  python eval/run_eval.py --mock                   # LLM mock'lanır, altyapıyı test eder
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb

EVAL_DIR = Path(__file__).parent
ROOT_DIR = EVAL_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

GOLDEN_SET = EVAL_DIR / "golden_set.jsonl"
REPORT_PATH = EVAL_DIR / "report.md"

# LLM judge için max satır — prompt'u şişirmemek için
_JUDGE_MAX_ROWS = 15


# ---------------------------------------------------------------------------
# Veri yükleme
# ---------------------------------------------------------------------------

def load_questions(ids: list[str] | None = None) -> list[dict[str, Any]]:
    questions = []
    with GOLDEN_SET.open() as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    if ids:
        questions = [q for q in questions if q["id"] in ids]
    return questions


# ---------------------------------------------------------------------------
# Ground truth çalıştırıcı
# ---------------------------------------------------------------------------

def run_expected(sql: str) -> list[dict[str, Any]] | None:
    """expected_sql'i doğrudan DuckDB'de çalıştırır."""
    try:
        con = duckdb.connect()
        cursor = con.execute(sql)
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        print(f"  [expected_sql ERROR] {e}")
        return None


# ---------------------------------------------------------------------------
# Aşama 1 — Exact match
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def results_match(agent_rows: list[dict[str, Any]], expected_rows: list[dict[str, Any]]) -> bool:
    """Sıra ve kolon adı bağımsız değer kümesi karşılaştırması."""
    def value_set(rows: list[dict[str, Any]]) -> list[tuple[str, ...]]:
        return sorted(
            tuple(sorted(str(_normalize(v)) for v in row.values()))
            for row in rows
        )
    return value_set(agent_rows) == value_set(expected_rows)


# ---------------------------------------------------------------------------
# Aşama 2 — LLM judge (yalnızca exact match başarısız olduğunda)
# ---------------------------------------------------------------------------

async def llm_judge(
    question: str,
    agent_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
) -> tuple[bool, str]:
    """
    gpt-4o-mini'ye her iki sonucu gönderir, soruyu doğru yanıtlayıp
    yanıtlamadığını structured output ile sorar.

    Returns:
        (correct: bool, reason: str)
    """
    from pydantic import BaseModel
    from langchain_openai import ChatOpenAI
    from src.config import get_settings

    class JudgeOutput(BaseModel):
        correct: bool
        reason: str

    s = get_settings()
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=s.openai_api_key.get_secret_value(),
    ).with_structured_output(JudgeOutput)

    agent_preview = json.dumps(agent_rows[:_JUDGE_MAX_ROWS], ensure_ascii=False, default=str)
    expected_preview = json.dumps(expected_rows[:_JUDGE_MAX_ROWS], ensure_ascii=False, default=str)

    prompt = f"""Sen bir SQL eval hakemisin. Aşağıdaki soruya verilen agent cevabının doğru olup olmadığına karar ver.

SORU: {question}

AGENT SONUCU ({len(agent_rows)} satır, ilk {min(len(agent_rows), _JUDGE_MAX_ROWS)} gösteriliyor):
{agent_preview}

REFERANS SONUÇ ({len(expected_rows)} satır, ilk {min(len(expected_rows), _JUDGE_MAX_ROWS)} gösteriliyor):
{expected_preview}

Değerlendirme kriterleri:
- Soruyu doğru yanıtlıyor mu? (aynı satırlar, aynı sayısal değerler)
- Kolon adları farklı olabilir — değerler önemli
- Tarih formatı farklı olabilir (2024-01 vs 2024-01-01) ama aynı dönemi temsil ediyorsa kabul et
- Fazla kolon varsa (agent expected'dan fazla döndürdüyse) sorun değil
- Satır sayısı ve değerler uyuşuyorsa correct=true
- Agent 0 satır döndürdüyse ve referans satır içeriyorsa correct=false"""

    try:
        result: JudgeOutput = await llm.ainvoke(prompt)
        return result.correct, result.reason
    except Exception as e:
        return False, f"Judge hatası: {e}"



# Mock LLM (--mock modu için)
class _MockSQLLLM:
    async def ainvoke(self, _: Any) -> Any:
        from src.agent.llm import SQLOutput
        return SQLOutput(source="duckdb", sql="SELECT 1 AS mock_only", rationale="mock")


class _MockSummaryLLM:
    async def ainvoke(self, _: Any) -> Any:
        class R:
            content = "Mock özet."
        return R()


# Tek soru değerlendirici
async def evaluate_one(question: dict[str, Any], mock: bool) -> dict[str, Any]:
    from src.agent.graph import run as agent_run

    start = time.perf_counter()
    try:
        if mock:
            with (
                patch("src.agent.nodes.get_sql_llm", return_value=_MockSQLLLM()),
                patch("src.agent.nodes.get_summary_llm", return_value=_MockSummaryLLM()),
            ):
                state = await agent_run(question["question"])
        else:
            state = await agent_run(question["question"])
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "id": question["id"],
            "correct": False,
            "method": "error",
            "judge_reason": None,
            "retries": 0,
            "latency": round(elapsed, 2),
            "error": str(e)[:120],
            "agent_sql": None,
        }

    elapsed = time.perf_counter() - start
    agent_result = state.get("result")
    agent_rows: list[dict[str, Any]] = agent_result.data if agent_result else []
    expected_rows = run_expected(question["expected_sql"])

    correct = False
    method = "exact"
    judge_reason: str | None = None

    if agent_result and agent_result.status == "success" and expected_rows is not None:
        if results_match(agent_rows, expected_rows):
            correct = True
        elif not mock:
            # Exact match başarısız → LLM judge devreye girer
            method = "llm_judge"
            correct, judge_reason = await llm_judge(
                question=question["question"],
                agent_rows=agent_rows,
                expected_rows=expected_rows,
            )

    return {
        "id": question["id"],
        "correct": correct,
        "method": method,
        "judge_reason": judge_reason,
        "retries": max(0, state.get("attempt", 1) - 1),
        "latency": round(elapsed, 2),
        "error": None,
        "agent_sql": state.get("sql", ""),
    }



# Rapor yazıcı (report.md)
def write_report(
    results: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    model: str,
    mock: bool,
) -> None:
    q_map = {q["id"]: q for q in questions}
    correct_count = sum(1 for r in results if r["correct"])
    exact_count = sum(1 for r in results if r["correct"] and r.get("method") == "exact")
    judge_count = sum(1 for r in results if r["correct"] and r.get("method") == "llm_judge")
    total = len(results)
    accuracy = round(100 * correct_count / total, 1) if total else 0
    avg_retries = round(sum(r["retries"] for r in results) / total, 2) if total else 0
    latencies = sorted(r["latency"] for r in results)
    p95_idx = min(int(math.ceil(0.95 * len(latencies))) - 1, len(latencies) - 1)
    p95 = latencies[p95_idx] if latencies else 0

    lines = [
        "# Eval Report",
        "",
        f"**Tarih:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Model:** {model}  ",
        f"**Mod:** {'mock (LLM mock\'landı)' if mock else 'live (hibrit: exact + llm_judge)'}  ",
        "",
        "## Özet",
        "",
        "| Metrik | Değer |",
        "|--------|-------|",
        f"| Accuracy | **{correct_count}/{total} ({accuracy}%)** |",
        f"| — exact match | {exact_count} |",
        f"| — llm_judge kurtardı | {judge_count} |",
        f"| Ort. retry | {avg_retries} |",
        f"| p95 latency | {p95}s |",
        "",
        "## Soru Bazında Sonuçlar",
        "",
        "| ID | Zorluk | Doğru | Yöntem | Retry | Süre (s) | Skills |",
        "|----|--------|-------|--------|-------|----------|--------|",
    ]

    for r in results:
        q = q_map.get(r["id"], {})
        icon = "✅" if r["correct"] else "❌"
        method = r.get("method", "exact")
        method_label = "exact" if method == "exact" else ("🤖judge" if method == "llm_judge" else method)
        skills = ", ".join(q.get("skills", []))
        err = f" `{r['error']}`" if r.get("error") else ""
        lines.append(
            f"| {r['id']} | {q.get('difficulty','?')} | {icon}{err} | {method_label} | "
            f"{r['retries']} | {r['latency']} | {skills} |"
        )

    lines += ["", "## Agent SQL Çıktıları", ""]
    for r in results:
        q = q_map.get(r["id"], {})
        lines.append(f"### {r['id']} — {q.get('question','')}")
        if r.get("judge_reason"):
            lines.append(f"> **Judge:** {r['judge_reason']}")
            lines.append("")
        lines.append("```sql")
        lines.append(r.get("agent_sql") or "(yok)")
        lines.append("```")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nRapor yazıldı: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Ana akış
async def main(question_ids: list[str] | None, mock: bool) -> None:
    from src.config import get_settings
    s = get_settings()

    questions = load_questions(question_ids)
    if not questions:
        print("Soru bulunamadı.")
        return

    print(f"Model: {s.openai_model} | Mod: {'mock' if mock else 'live (hibrit)'} | Soru: {len(questions)}")
    print("-" * 60)

    results = []
    for q in questions:
        print(f"[{q['id']}] {q['question'][:60]}...")
        result = await evaluate_one(q, mock=mock)
        icon = "✅" if result["correct"] else "❌"
        method = result.get("method", "exact")
        tag = f" [{method}]" if result["correct"] and method == "llm_judge" else ""
        print(f"  {icon}{tag} retry={result['retries']} latency={result['latency']}s")
        if result.get("judge_reason") and not result["correct"]:
            print(f"  judge: {result['judge_reason'][:80]}")
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
        results.append(result)

    correct = sum(1 for r in results if r["correct"])
    exact = sum(1 for r in results if r["correct"] and r.get("method") == "exact")
    judge = sum(1 for r in results if r["correct"] and r.get("method") == "llm_judge")
    print(f"\nSonuç: {correct}/{len(results)} doğru  (exact={exact}, llm_judge={judge})")

    write_report(results, questions, model=s.openai_model, mock=mock)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Text-to-SQL eval harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true", help="Gerçek LLM kullan")
    group.add_argument("--mock", action="store_true", help="LLM mock'la (altyapı testi)")
    parser.add_argument("--questions", help="Virgülle ayrılmış soru ID'leri (örn: q01,q03)")
    args = parser.parse_args()

    ids = [q.strip() for q in args.questions.split(",")] if args.questions else None
    asyncio.run(main(ids, mock=args.mock))