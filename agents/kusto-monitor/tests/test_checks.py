"""Unit tests for check loading and evaluation (no Kusto needed)."""

import pytest

from kusto_monitor.checks import Check, load_checks, run_check, run_checks


def write(tmp_path, text):
    path = tmp_path / "checks.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_shipped_checks_file_is_valid():
    checks = load_checks("checks.yaml")
    names = [c.name for c in checks]
    assert names == ["high-error-rate", "high-latency-p95", "service-silent", "error-count-anomaly"]
    assert all(c.query.startswith(("AppTelemetry", "let")) for c in checks)


def test_load_rejects_missing_query(tmp_path):
    with pytest.raises(ValueError, match="missing 'query'"):
        load_checks(write(tmp_path, "checks:\n  - name: a\n"))


def test_load_rejects_bad_severity(tmp_path):
    with pytest.raises(ValueError, match="invalid severity"):
        load_checks(write(tmp_path, "checks:\n  - name: a\n    query: T\n    severity: panic\n"))


def test_load_rejects_duplicates(tmp_path):
    text = "checks:\n  - name: a\n    query: T\n  - name: a\n    query: T\n"
    with pytest.raises(ValueError, match="Duplicate"):
        load_checks(write(tmp_path, text))


def test_rows_mean_alert_and_no_rows_mean_ok():
    assert run_check(lambda q: [], Check("c", "T")).status == "ok"
    result = run_check(lambda q: [{"Service": "payments"}], Check("c", "T"))
    assert result.status == "alert" and result.is_alert
    assert result.rows == [{"Service": "payments"}]


def test_query_failure_becomes_error_not_crash():
    def boom(_):
        raise RuntimeError("Semantic error: 'Foo' is not a table\nmore detail")

    result = run_check(boom, Check("c", "Foo"))
    assert result.status == "error"
    assert result.error == "Semantic error: 'Foo' is not a table"


def test_run_checks_skips_disabled_and_filters():
    checks = [Check("a", "A"), Check("b", "B", enabled=False), Check("c", "C")]
    seen = []

    def query(q):
        seen.append(q)
        return []

    assert [r.check.name for r in run_checks(query, checks)] == ["a", "c"]
    assert [r.check.name for r in run_checks(query, checks, only=["c"])] == ["c"]
    with pytest.raises(ValueError, match="Unknown check"):
        run_checks(query, checks, only=["nope"])
