##--Uygulama başında bir kez çağrıldığında; her log kaydını konsola insan-okur renkli formatta, logs/copilot.log dosyasına ise JSON satırı olarak yazar.--##

"""
Neden print() yetmez?
  - print çıktısı yapılandırılmamış — 3 ay sonra "hangi tool hangi SQL'i ne kadar sürede çalıştırdı" sorusuna cevap veremezsin.
  - structlog her log kaydını {"event": "sql_executed", "tool": "query_sql", "rows": 42, "elapsed_ms": 37} şeklinde saklar. grep, jq, Datadog, Loki → hepsi bunu okur.
  - Bound logger paterni: bir kere log = get_logger(__name__, tool="query_sql") dedin mi, o logger'dan çıkan her satırda tool="query_sql" otomatik yer alır. Manuel tekrar yok.
  - İki hedef: konsol (dev deneyimi) + dosya (kalıcı iz).
"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog

from src.config import get_settings

_configured = False


def configure_logging() -> None:
    global _configured

    if _configured:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level)
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    console_handler = logging.StreamHandler(
        sys.stderr
    )  #  MCP sunucusu stdout üzerinden JSON-RPC konuşur. Eğer log'ları stdout'a atarsan MCP protokolünü kirletirsin, client parse edemez. Loglama daima stderr'e.
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
        )
    )

    file_hanler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_hanler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_hanler)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str, **context: Any) -> structlog.stdlib.BoundLogger:
    """
    Bound logger döner; kwargs kalıcı context olarak log'a yapışır.

    Örnek:
        log = get_logger(__name__, tool="query_sql")
        log.info("executing", rows=42, elapsed_ms=37)
    """
    if not _configured:
        configure_logging()
    # cast: structlog stub'ları bind() için Any döner; runtime'da BoundLogger.
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name).bind(**context))
