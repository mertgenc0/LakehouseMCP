"""Lakehouse Copilot — CLI entrypoint.

Kullanım:
    python main.py                            # interaktif REPL
    python main.py --question "..."           # tek soru sor, çık
    python main.py -q "..."                   # kısa form
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from src.agent.state import AgentState
from src.config import get_settings
from src.core.logging import configure_logging, get_logger
from src.core.tracing import init_tracing

app = typer.Typer(
    add_completion=False,
    help="Lakehouse Copilot — doğal dilde veri sorgulama.",
)
console = Console()
log = get_logger(__name__, component="cli")



# Rendering                                                                    #
def _render_state(state: AgentState) -> None:
    """Agent state'ini kullanıcıya renkli panellerle sunar."""
    question = state.get("question", "")
    sql = state.get("sql")
    result = state.get("result")
    answer = state.get("final_answer", "")
    attempts = state.get("attempt", 0)

    console.print()
    console.print(Panel(question, title="[bold]Soru[/bold]", border_style="cyan"))

    if sql:
        source = state.get("source", "duckdb")
        title = f"[bold]Üretilen SQL[/bold] [magenta]› {source}[/magenta]"
        if attempts > 1:
            title += f" [dim](deneme {attempts})[/dim]"
        console.print(
            Panel(
                Syntax(sql, "sql", theme="monokai", word_wrap=True),
                title=title,
                border_style="blue",
            )
        )

    if result is not None and result.data:
        table = Table(
            title=f"[bold]Sonuç[/bold] ({result.row_count} satır, {result.elapsed_ms} ms)",
            border_style="green",
            header_style="bold green",
        )
        for col in result.columns:
            table.add_column(col)
        for row in result.data[:20]:
            table.add_row(*(str(row.get(c, "")) for c in result.columns))
        console.print(table)
        if result.row_count > 20:
            console.print(f"[dim]... {result.row_count - 20} satır daha (ilk 20 gösterildi)[/dim]")
    elif result is not None:
        console.print("[yellow]Sorgu çalıştı ama sonuç boş.[/yellow]")

    if answer:
        console.print(Panel(Markdown(answer), title="[bold]Cevap[/bold]", border_style="yellow"))



# Runners
async def _approval_callback(payload: dict) -> str:
    """human_approval interrupt'ı tetiklendiğinde kullanıcıya sorar."""
    estimated = payload.get("estimated_rows", 0)
    threshold = payload.get("threshold", 0)
    sql_preview = str(payload.get("sql", ""))[:120]
    console.print()
    console.print(
        Panel(
            f"[yellow]Bu sorgu tahminen [bold]{estimated:,}[/bold] satır döndürecek "
            f"(eşik: {threshold:,}).[/yellow]\n\n"
            f"[dim]{sql_preview}...[/dim]",
            title="[bold yellow]Onay Gerekiyor[/bold yellow]",
            border_style="yellow",
        )
    )
    return console.input("[bold yellow]Devam etmek istiyor musunuz? (e/h): [/bold yellow]").strip()


async def _run_one(question: str) -> None:
    """Tek soru için agent'ı çalıştırır, sonucu render eder."""
    from src.agent.graph import run  # noqa: PLC0415 — tracing init'ten sonra yüklenir
    try:
        with console.status("[cyan]Düşünüyorum...[/cyan]", spinner="dots"):
            state = await run(question, approval_callback=_approval_callback)
    except Exception as exc:
        log.exception("run_failed", question=question[:120])
        console.print(Panel(f"[red]Hata:[/red] {exc}", border_style="red"))
        return
    _render_state(state)


async def _repl() -> None:
    """İnteraktif REPL — kullanıcı exit/quit/Ctrl+C ile çıkar."""
    console.print(
        Panel.fit(
            "[bold cyan]Lakehouse Copilot[/bold cyan]\n"
            "Doğal dilde veri sorularınızı sorun. Çıkmak için "
            "[dim]exit[/dim], [dim]quit[/dim] veya [dim]Ctrl+C[/dim].",
            border_style="cyan",
        )
    )
    while True:
        try:
            question = console.input("\n[bold cyan]soru › [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Görüşürüz.[/dim]")
            return
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            console.print("[dim]Görüşürüz.[/dim]")
            return
        await _run_one(question)



# CLI
@app.command()
def cli(
    question: str = typer.Option(None, "--question", "-q", help="Tek soru sor ve çık."),
) -> None:
    """Lakehouse Copilot CLI."""
    configure_logging()
    init_tracing()
    try:
        get_settings()
    except Exception as exc:
        console.print(f"[red]Yapılandırma hatası:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if question:
        asyncio.run(_run_one(question))
    else:
        asyncio.run(_repl())


if __name__ == "__main__":
    app()
