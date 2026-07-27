"""
Exception construction + attribute + str() davranışı.

 * CopilotError: Alt sınıflar hepsi CopilotError, KeyboardInterrupt yakalanmıyor
 * GuardrailViolation: reason/query attribute, str format, CopilotError olarak yakalanabilir.
 * SQLExecutionError: Keyword-only args zorunlu, query/engine attribute, [engine] prefix
 * MCPConnectionError: Basit mesaj, exception chaining (from)
 * MaxRetriesExceeded: attempts/last_error attribute, str'de rakam + hata görünür

"""
from __future__ import annotations

import pytest

from src.core.exceptions import (
    CopilotError,
    GuardrailViolation,
    MaxRetriesExceeded,
    MCPConnectionError,
    SQLExecutionError,
)


class TestCopilotError:
    def test_is_base_class(self):
        # Alt sınıflar CopilotError yakalanabilmeli
        assert issubclass(GuardrailViolation, CopilotError)
        assert issubclass(SQLExecutionError, CopilotError)
        assert issubclass(MCPConnectionError, CopilotError)
        assert issubclass(MaxRetriesExceeded, CopilotError)

    def test_not_caught_by_narrow_except(self):
        # CopilotError, KeyboardInterrupt vb. yakalamamalı
        assert not issubclass(CopilotError, KeyboardInterrupt)


class TestGuardrailViolation:
    def test_reason_attribute(self):
        err = GuardrailViolation("test reason")
        assert err.reason == "test reason"
        assert err.query is None

    def test_query_attribute(self):
        err = GuardrailViolation("bad", query="SELECT foo")
        assert err.query == "SELECT foo"

    def test_str_contains_reason(self):
        err = GuardrailViolation("yasak kelime")
        assert "yasak kelime" in str(err)

    def test_can_be_caught_as_copilot_error(self):
        with pytest.raises(CopilotError):
            raise GuardrailViolation("boş sql")


class TestSQLExecutionError:
    def test_keyword_only_args_required(self):
        # positional args yasak: query ve engine keyword-only
        with pytest.raises(TypeError):
            SQLExecutionError("msg", "SELECT 1", "duckdb")

    def test_attributes(self):
        err = SQLExecutionError("column not found", query="SELECT foo", engine="duckdb")
        assert err.query == "SELECT foo"
        assert err.engine == "duckdb"

    def test_str_includes_engine(self):
        err = SQLExecutionError("boom", query="SELECT 1", engine="postgres")
        assert "[postgres]" in str(err)
        assert "boom" in str(err)


class TestMCPConnectionError:
    def test_simple_message(self):
        err = MCPConnectionError("stdio kapandı")
        assert "stdio kapandı" in str(err)

    def test_from_chain(self):
        try:
            try:
                raise OSError("pipe broken")
            except OSError as inner:
                raise MCPConnectionError("connection lost") from inner
        except MCPConnectionError as e:
            assert e.__cause__ is not None
            assert isinstance(e.__cause__, OSError)


class TestMaxRetriesExceeded:
    def test_attributes(self):
        err = MaxRetriesExceeded(attempts=3, last_error="col not found")
        assert err.attempts == 3
        assert err.last_error == "col not found"

    def test_str_shows_attempts_and_error(self):
        err = MaxRetriesExceeded(attempts=5, last_error="parse fail")
        s = str(err)
        assert "5" in s
        assert "parse fail" in s