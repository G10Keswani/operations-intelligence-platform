"""Create the tables the agent and the traffic simulator use.

Usage:
    python -m kusto_monitor.setup_db                    # existing database (Azure)
    python -m kusto_monitor.setup_db --create-database  # local emulator only
"""

import argparse

from .kusto import executor_from_env

SCHEMA_COMMANDS = [
    ".create-merge table AppTelemetry ("
    "Timestamp: datetime, Service: string, Region: string, Endpoint: string, "
    "StatusCode: int, DurationMs: real)",
    ".create-merge table MonitorAlerts ("
    "Timestamp: datetime, Check: string, Severity: string, Description: string, "
    "RowCount: long, Details: dynamic)",
]


def main(argv=None) -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument(
        "--create-database",
        action="store_true",
        help="Create the database first (Kusto emulator only; in Azure create it with az/portal).",
    )
    args = parser.parse_args(argv)

    kusto = executor_from_env()
    if args.create_database:
        existing = {row["DatabaseName"] for row in kusto.command(".show databases", database="NetDefaultDB")}
        if kusto.database not in existing:
            kusto.command(f".create database {kusto.database} volatile", database="NetDefaultDB")
            print(f"Created database {kusto.database}")

    for command in SCHEMA_COMMANDS:
        kusto.command(command)
        print(f"OK: {command.split('(', maxsplit=1)[0].strip()}")
    print(f"Schema ready in database {kusto.database}.")


if __name__ == "__main__":
    main()
