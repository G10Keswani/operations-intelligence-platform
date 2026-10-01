"""Thin wrapper around the Azure Data Explorer (Kusto) client."""

import datetime
import os
from typing import Any, Dict, List, Optional

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

DEFAULT_DATABASE = "MonitoringDb"


def _jsonable(value: Any) -> Any:
    """Convert Kusto values (datetime, timedelta, ...) into JSON-friendly values."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


class KustoExecutor:
    """Runs queries and management commands against one Kusto database."""

    def __init__(self, client: KustoClient, database: str):
        self.client = client
        self.database = database

    def query(self, kql: str) -> List[Dict[str, Any]]:
        """Run a KQL query and return the primary result as a list of dicts."""
        response = self.client.execute_query(self.database, kql)
        return _rows(response)

    def command(self, command: str, database: Optional[str] = None) -> List[Dict[str, Any]]:
        """Run a management (dot) command and return the primary result."""
        response = self.client.execute_mgmt(database or self.database, command)
        return _rows(response)


def _rows(response) -> List[Dict[str, Any]]:
    if not response.primary_results:
        return []
    table = response.primary_results[0]
    return [{k: _jsonable(v) for k, v in row.to_dict().items()} for row in table]


def build_client(cluster_url: str, auth: str = "default") -> KustoClient:
    """Create a KustoClient.

    auth:
      default - DefaultAzureCredential (az login locally, managed identity in Azure)
      cli     - Azure CLI login only
      none    - no auth, for the local Kusto emulator (http://localhost:8080)
    """
    if auth == "none":
        kcsb = KustoConnectionStringBuilder.with_no_authentication(cluster_url)
    elif auth == "cli":
        kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(cluster_url)
    elif auth == "default":
        # Imported lazily so the emulator path does not need azure-identity configured.
        from azure.identity import DefaultAzureCredential  # pylint: disable=import-outside-toplevel

        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            cluster_url, DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
    else:
        raise ValueError(f"Unknown KUSTO_AUTH mode: {auth!r} (use default, cli or none)")
    return KustoClient(kcsb)


def executor_from_env() -> KustoExecutor:
    """Build an executor from KUSTO_CLUSTER_URL / KUSTO_DATABASE / KUSTO_AUTH."""
    cluster_url = os.environ.get("KUSTO_CLUSTER_URL")
    if not cluster_url:
        raise SystemExit(
            "KUSTO_CLUSTER_URL is not set. Example: "
            "export KUSTO_CLUSTER_URL=https://<cluster>.<region>.kusto.windows.net"
        )
    database = os.environ.get("KUSTO_DATABASE", DEFAULT_DATABASE)
    auth = os.environ.get("KUSTO_AUTH", "default")
    return KustoExecutor(build_client(cluster_url, auth), database)
