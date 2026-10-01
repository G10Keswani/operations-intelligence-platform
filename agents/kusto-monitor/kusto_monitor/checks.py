"""Load health checks from YAML and evaluate them.

A check is a KQL query that returns one row per problem it finds.
No rows -> the check is OK. One or more rows -> the check raises an alert.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import yaml

SEVERITIES = ("info", "warning", "critical")

QueryFn = Callable[[str], List[Dict[str, Any]]]


@dataclass
class Check:
    """One KQL health check."""

    name: str
    query: str
    severity: str = "warning"
    description: str = ""
    enabled: bool = True


@dataclass
class CheckResult:
    """Outcome of running one check."""

    check: Check
    status: str  # "ok", "alert" or "error"
    rows: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def is_alert(self) -> bool:
        """True when the check found problems."""
        return self.status == "alert"


def load_checks(path: str) -> List[Check]:
    """Read and validate a checks YAML file."""
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    checks = []
    seen = set()
    for index, raw in enumerate(data.get("checks", [])):
        for key in ("name", "query"):
            if not raw.get(key):
                raise ValueError(f"Check #{index + 1} in {path} is missing '{key}'")
        check = Check(
            name=str(raw["name"]),
            query=str(raw["query"]).strip(),
            severity=str(raw.get("severity", "warning")).lower(),
            description=str(raw.get("description", "")).strip(),
            enabled=bool(raw.get("enabled", True)),
        )
        if check.severity not in SEVERITIES:
            raise ValueError(f"Check '{check.name}' has invalid severity '{check.severity}' (use {SEVERITIES})")
        if check.name in seen:
            raise ValueError(f"Duplicate check name '{check.name}' in {path}")
        seen.add(check.name)
        checks.append(check)

    if not checks:
        raise ValueError(f"No checks defined in {path}")
    return checks


def run_check(query_fn: QueryFn, check: Check) -> CheckResult:
    """Run one check. Query failures become an 'error' result instead of crashing the agent."""
    started = time.monotonic()
    try:
        rows = query_fn(check.query)
    except Exception as exc:  # pylint: disable=broad-except
        return CheckResult(check, "error", error=str(exc).splitlines()[0][:500],
                           duration_ms=(time.monotonic() - started) * 1000)
    status = "alert" if rows else "ok"
    return CheckResult(check, status, rows=rows, duration_ms=(time.monotonic() - started) * 1000)


def run_checks(query_fn: QueryFn, checks: List[Check], only: Optional[List[str]] = None) -> List[CheckResult]:
    """Run every enabled check (or only the named ones)."""
    selected = [c for c in checks if c.enabled and (not only or c.name in only)]
    if only:
        unknown = set(only) - {c.name for c in checks}
        if unknown:
            raise ValueError(f"Unknown check(s): {', '.join(sorted(unknown))}")
    return [run_check(query_fn, check) for check in selected]
