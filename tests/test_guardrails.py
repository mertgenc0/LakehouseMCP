"""
SQL validation ve path traversal koruması — happy + error path'leri.

 * Happy path — LIMIT eklenmesi, LIMIT korunması, WITH cümlesi, trailing ;, case-insensitive başlangıç, string literal içinde DROP, updated_at kolon adı,
satır yorumu, blok yorumu

 * Rejection — DELETE, UPDATE, INSERT, multi-statement (; sonrası DROP), multi-SELECT, ortada yasak kelime, boş SQL, sadece whitespace, sadece yorum

 * Path validation — geçerli path, ../.. traversal, mutlak path (/tmp/...), home expansion (~/...)
"""

from __future__ import annotations

import pytest

from src.core.exceptions import GuardrailViolation
from src.core.guardrails import validate_path, validate_sql


class TestValidateSqlHappyPath:
    def test_simple_select_gets_default_limit(self):
        result = validate_sql("SELECT * FROM t")
        assert result.endswith("LIMIT 1000")

    def test_existing_limit_preserved(self):
        result = validate_sql("SELECT * FROM t LIMIT 5")
        assert "LIMIT 5" in result
        assert "LIMIT 1000" not in result

    def test_limit_with_offset_preserved(self):
        result = validate_sql("SELECT * FROM t LIMIT 5 OFFSET 10")
        assert "LIMIT 1000" not in result
        assert "OFFSET 10" in result

    def test_with_clause_allowed(self):
        result = validate_sql("WITH x AS (SELECT 1) SELECT * FROM x")
        assert result.lower().startswith("with")

    def test_trailing_semicolon_stripped(self):
        result = validate_sql("SELECT 1;")
        assert not result.endswith(";")

    def test_case_insensitive_start(self):
        result = validate_sql("select id from t")
        assert "select id from t" in result

    def test_string_literal_containing_drop_allowed(self):
        # String içindeki 'DROP TABLE' yasak kelime SAYILMAMALI
        result = validate_sql("SELECT 'DROP TABLE users' AS msg FROM t")
        assert "'DROP TABLE users'" in result

    def test_updated_at_column_not_flagged(self):
        # 'updated_at' 'update' içerir ama tek token; yasaklı sayılmamalı
        result = validate_sql("SELECT id, updated_at FROM orders")
        assert "updated_at" in result

    def test_comment_stripped_from_output(self):
        result = validate_sql("SELECT 1 -- yorum satırı\nFROM t")
        assert "yorum satırı" not in result

    def test_block_comment_stripped(self):
        result = validate_sql("SELECT 1 /* açıklama */ FROM t")
        assert "açıklama" not in result


class TestValidateSqlRejections:
    def test_delete_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"SELECT/WITH"):
            validate_sql("DELETE FROM t")

    def test_update_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"SELECT/WITH"):
            validate_sql("UPDATE t SET x = 1")

    def test_insert_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"SELECT/WITH"):
            validate_sql("INSERT INTO t VALUES (1)")

    def test_multi_statement_with_drop_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"Birden [fF]azla"):
            validate_sql("SELECT 1; DROP TABLE users")

    def test_multi_select_statement_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"Birden [fF]azla"):
            validate_sql("SELECT 1; SELECT 2")

    def test_banned_keyword_in_middle_rejected(self):
        # SELECT ile başlasa da içinde DROP geçerse red
        with pytest.raises(GuardrailViolation, match=r"[yY]asaklı"):
            validate_sql("SELECT * FROM t WHERE drop_flag = 1 AND drop = TRUE")

    def test_empty_sql_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"[bB]oş"):
            validate_sql("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"[bB]oş"):
            validate_sql("   \n  \t  ")

    def test_only_comment_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"[bB]oş"):
            validate_sql("-- sadece yorum")


class TestValidatePath:
    def test_valid_path_under_data_dir_resolves(self):
        # seed_data ile üretilen dosya
        result = validate_path("data/processed/orders.parquet")
        assert result.is_absolute()
        assert result.name == "orders.parquet"

    def test_path_traversal_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"DATA_DIR"):
            validate_path("../../etc/passwd")

    def test_absolute_path_outside_rejected(self):
        with pytest.raises(GuardrailViolation, match=r"DATA_DIR"):
            validate_path("/tmp/definitely-not-in-data-dir.parquet")  # noqa: S108 — test path traversal

    def test_home_expansion_rejected(self):
        # ~/foo, DATA_DIR dışında olduğu için red
        with pytest.raises(GuardrailViolation, match=r"DATA_DIR"):
            validate_path("~/foo.parquet")
