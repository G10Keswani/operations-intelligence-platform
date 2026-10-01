# First Kusto Monitoring Agent on Azure: Step-by-Step

This guide takes you from nothing to a working **monitoring agent** that:

1. runs **KQL health checks** against **Azure Data Explorer (Kusto)** on a schedule,
2. detects **error spikes, slow services, silent services and statistical anomalies**,
3. sends alerts to **console, Microsoft Teams/Slack**, and an **alert-history table in Kusto**,
4. runs in Azure as a **Container Apps scheduled job** that uses a **managed identity** (no passwords).

The guide includes a **traffic simulator** that injects incidents when you ask it to, so you can
**demo it live** and **test it automatically**.

```
 ┌──────────────────┐   .set-or-append    ┌──────────────────────────────┐
 │ simulate.py      │ ──────────────────▶ │ Azure Data Explorer           │
 │ (fake app traffic│                     │  DB: MonitoringDb             │
 │  + incidents)    │                     │   ├─ AppTelemetry  (raw data) │
 └──────────────────┘                     │   └─ MonitorAlerts (history)  │
                                          └───────────▲──────────┬───────┘
                       KQL checks (checks.yaml)       │          │ .ingest inline
                                          ┌───────────┴──────────▼───────┐      ┌───────────────┐
                                          │ Kusto Monitoring Agent       │ ───▶ │ Teams / Slack │
                                          │ Azure Container Apps Job     │      └───────────────┘
                                          │ cron */5 · managed identity  │ ───▶ console logs (Log Analytics)
                                          └──────────────────────────────┘
```

| | |
|---|---|
| **Time** | ~20 min local (emulator) · ~60 min full Azure deployment (most of it is waiting for the cluster) |
| **Cost** | Emulator: free. Azure: ADX *Dev (No SLA)* cluster is billed per hour while running, plus a few cents for Container Apps/ACR. **Stop or delete it after the demo** (see [Step 12](#step-12--cost-control--cleanup)). |
| **You need** | Python 3.9+, Azure CLI 2.60+, an Azure subscription (Contributor on a resource group). Docker is optional (only for the local emulator). |

---

## Contents

- [Folder layout](#folder-layout)
- [Step 0: Prerequisites](#step-0--prerequisites)
- [Step 1: Get the code and install](#step-1--get-the-code-and-install)
- [Step 2: Create a Kusto cluster (pick a path)](#step-2--create-a-kusto-cluster-pick-a-path)
- [Step 3: Connect and create the tables](#step-3--connect-and-create-the-tables)
- [Step 4: Generate telemetry](#step-4--generate-telemetry)
- [Step 5: Understand the checks](#step-5--understand-the-checks)
- [Step 6: Run the agent: everything green](#step-6--run-the-agent-everything-green)
- [Step 7: Break things and watch the agent catch it](#step-7--break-things-and-watch-the-agent-catch-it)
- [Step 8: Send alerts to Teams and keep history in Kusto](#step-8--send-alerts-to-teams-and-keep-history-in-kusto)
- [Step 9: Deploy the agent to Azure (Container Apps Job)](#step-9--deploy-the-agent-to-azure-container-apps-job)
- [Step 10: Test the deployed agent end to end](#step-10--test-the-deployed-agent-end-to-end)
- [Step 11: Automated tests](#step-11--automated-tests)
- [Step 12: Cost control and cleanup](#step-12--cost-control--cleanup)
- [10-minute demo script](#10-minute-demo-script)
- [Add your own check](#add-your-own-check)
- [Troubleshooting](#troubleshooting)

---

## Folder layout

```
agents/kusto-monitor/
├── checks.yaml                  # the health checks (pure KQL), the agent's "brain"
├── kusto_monitor/
│   ├── agent.py                 # CLI: run checks, dispatch alerts, exit codes
│   ├── checks.py                # load YAML, run queries, rows => alert
│   ├── notifiers.py             # console / webhook (Teams, Slack) / MonitorAlerts table
│   ├── kusto.py                 # Kusto client + auth (az CLI, managed identity, emulator)
│   ├── setup_db.py              # create tables
│   └── simulate.py              # synthetic traffic + incident injection
├── kql/
│   ├── setup.kql                # same schema, for the ADX web UI
│   └── demo-queries.kql         # charts to show during the demo
├── infra/
│   ├── 00-init-env.sh           # generates infra/env.sh with unique names
│   ├── 01-provision-adx.sh      # RG + ADX Dev cluster + DB + your Admin role
│   ├── 02-deploy-agent.sh       # ACR build + managed identity + Container Apps Job
│   ├── logs.sh                  # read the job's console output
│   └── 99-cleanup.sh            # delete everything
├── scripts/
│   ├── start-emulator.sh        # local Kusto emulator (Docker)
│   └── demo-local.sh            # guided 4-act demo
├── tests/                       # unit tests + emulator end-to-end tests
├── Dockerfile
└── requirements.txt
```

---

## Step 0: Prerequisites

```bash
python3 --version          # 3.9 or newer
az version                 # Azure CLI 2.60+  (install: https://aka.ms/azcli)
docker --version           # optional, only for the local emulator path
```

Sign in and pick the subscription you'll use:

```bash
az login
az account set --subscription "<subscription name or id>"
az account show --query "{name:name, id:id, user:user.name}" -o table
```

> You need permission to create resource groups, an ADX cluster, a container registry, a managed identity
> and **role assignments** (Owner, or Contributor + User Access Administrator, on the subscription or resource group).

---

## Step 1: Get the code and install

```bash
git clone https://github.com/G10Keswani/operations-intelligence-platform.git
cd operations-intelligence-platform/agents/kusto-monitor

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

**Check that it worked.** Run the unit tests. They need no Azure at all:

```bash
pytest -q
# 20 passed, 5 skipped   <- the 5 skipped are emulator tests (Step 11)
```

> Every command from here on is run from `agents/kusto-monitor/` with the venv active.

---

## Step 2: Create a Kusto cluster (pick a path)

| Path | Use it for | Time | Cost |
|---|---|---|---|
| **A. Azure ADX Dev cluster** (recommended) | The real Azure demo, including the deployed agent (Step 9) | ~15 min | hourly while running |
| **B. ADX free cluster** | Quick Azure-hosted demo of Steps 3–8 run from your laptop | ~2 min | free |
| **C. Local Kusto emulator** | Offline development and automated tests | ~2 min | free |

### Path A: Azure Data Explorer Dev cluster (scripted)

```bash
./infra/00-init-env.sh            # writes infra/env.sh with unique names (edit LOCATION if you want)
source infra/env.sh
./infra/01-provision-adx.sh       # ~10-20 min: RG, cluster, MonitoringDb, Admin role for you
```

The script ends by printing:

```
✅ Cluster URL: https://adxmonxxxxxx.eastus.kusto.windows.net
Next: export KUSTO_CLUSTER_URL=https://adxmonxxxxxx.eastus.kusto.windows.net KUSTO_DATABASE=MonitoringDb KUSTO_AUTH=cli
```

<details><summary>Prefer the portal? (click)</summary>

1. Portal → **Create a resource** → *Azure Data Explorer* → Create.
2. Resource group `rg-kusto-monitor-demo`, unique cluster name, region, **Workload: Dev/test** (Dev, No SLA).
3. Review + create (~10-20 min).
4. Open the cluster → **Databases** → **+ Add database** → `MonitoringDb`, retention 30 days, cache 7 days.
5. Copy the **URI** from the cluster *Overview* page.
</details>

### Path B: ADX free cluster

1. Go to <https://aka.ms/kustofree> and sign in.
2. **Create cluster**, then give the cluster and the database names (call the database `MonitoringDb`).
3. Copy the **Cluster URI** from the *My cluster* page.
4. Make sure `az login` uses the same account. Add `--allow-no-subscriptions` if that account has no subscription.

> For Step 9 (deploy to Azure) use Path A. The scheduled job's managed identity needs RBAC on a cluster in your subscription.

### Path C: Local Kusto emulator (Docker)

```bash
./scripts/start-emulator.sh       # pulls mcr.microsoft.com/azuredataexplorer/kustainer-linux (~2 GB first time)
```

---

## Step 3: Connect and create the tables

Tell the agent where Kusto is. `KUSTO_AUTH=cli` reuses your `az login`:

```bash
# Path A / B
export KUSTO_CLUSTER_URL="https://<your-cluster>.<region>.kusto.windows.net"
export KUSTO_DATABASE="MonitoringDb"
export KUSTO_AUTH="cli"
python -m kusto_monitor.setup_db

# Path C (emulator): no auth, and the script also creates the database
export KUSTO_CLUSTER_URL="http://localhost:8080" KUSTO_DATABASE="MonitoringDb" KUSTO_AUTH="none"
python -m kusto_monitor.setup_db --create-database
```

Expected:

```
OK: .create-merge table AppTelemetry
OK: .create-merge table MonitorAlerts
Schema ready in database MonitoringDb.
```

| Table | Columns | Purpose |
|---|---|---|
| `AppTelemetry` | Timestamp, Service, Region, Endpoint, StatusCode, DurationMs | One row per HTTP request: the data being monitored |
| `MonitorAlerts` | Timestamp, Check, Severity, Description, RowCount, Details (dynamic) | The agent's alert history |

**Check that it worked.** Open <https://dataexplorer.azure.com>, choose **+ Add → Connection**, paste the
cluster URI, and expand `MonitoringDb`. You should see both tables. For Path C, run
`.show tables` from Python or point the web UI at `http://localhost:8080`.

---

## Step 4: Generate telemetry

The simulator models four services (`checkout`, `payments`, `catalog`, `auth`) at about 4 requests per second
in total, with about 1% errors and 40–200 ms latency.

**Option 1: live stream (best for a live demo).** Leave it running in **terminal 1**:

```bash
python -m kusto_monitor.simulate live
# No recent data found: backfilling 6h of healthy history first...
# Streaming healthy traffic every 30s. Press Ctrl+C to stop.
#   appended 14:02:10..14:02:40 UTC (healthy traffic)
```

The first run backfills 6 hours of history, which the anomaly check uses to learn a baseline. After that it
appends fresh data every 30 seconds.

**Option 2: one-shot backfill (best for repeatable tests).**

```bash
python -m kusto_monitor.simulate backfill --hours 3
```

**Check that it worked.** In the ADX web UI, run query 1 from [`kql/demo-queries.kql`](kql/demo-queries.kql):

```kql
AppTelemetry
| where Timestamp > ago(1h)
| summarize Requests = count() by Service, bin(Timestamp, 1m)
| render timechart
```

You should see four flat lines at about 60 requests per minute.

> In **every other terminal** you open, run `source .venv/bin/activate` again and re-export the three `KUSTO_*` variables.

---

## Step 5: Understand the checks

The whole agent is driven by [`checks.yaml`](checks.yaml). The contract is simple:

> **Each check is a KQL query that returns one row per problem. 0 rows = OK. 1 or more rows = ALERT.**

| Check | Severity | Fires when | KQL technique |
|---|---|---|---|
| `high-error-rate` | critical | a service has > 5% HTTP 5xx in the last 5 min (min 20 requests) | `summarize countif()` |
| `high-latency-p95` | warning | a service's p95 latency is > 800 ms in the last 5 min | `percentile()` |
| `service-silent` | critical | a service reported in the last hour but not in the last 3 min | `max(Timestamp)` freshness |
| `error-count-anomaly` | warning | errors per minute break away from the 3-hour learned baseline | `make-series` + `series_decompose_anomalies` |

You can paste any query into the ADX web UI to see exactly what the agent sees. That makes a good moment in the demo.

---

## Step 6: Run the agent: everything green

In **terminal 2**:

```bash
python -m kusto_monitor.agent --once
```

Expected:

```
=== Kusto Monitoring Agent run @ 2026-10-01T14:05:12+00:00 ===
[OK   ] high-error-rate              critical rows=0     96 ms
[OK   ] high-latency-p95             warning  rows=0     32 ms
[OK   ] service-silent               critical rows=0     15 ms
[OK   ] error-count-anomaly          warning  rows=0    288 ms

Summary: 4 checks, 0 alert(s), 0 error(s)
```

Useful flags:

| Flag | What it does |
|---|---|
| `--once` | run once and exit (default) |
| `--interval 60` | run forever, every 60 s (a local "daemon" mode) |
| `--check high-error-rate` | run just one check (repeatable) |
| `--fail-on-alert` | exit code **2** when anything alerts, for scripts and CI |
| `--record-alerts` | also write alerts to `MonitorAlerts` |
| `--webhook-url URL --webhook-format teams` | post alerts to chat |

Exit codes: `0` OK · `2` alerts (only with `--fail-on-alert`) · `3` a check's query failed.

---

## Step 7: Break things and watch the agent catch it

Restart the simulator in **terminal 1** with an incident:

```bash
# Ctrl+C the healthy stream first, then pick one:
python -m kusto_monitor.simulate live --incident error-spike --service payments   # 40% HTTP 500s
python -m kusto_monitor.simulate live --incident latency     --service checkout   # +1.5 s per request
python -m kusto_monitor.simulate live --incident outage      --service auth       # service stops reporting
```

Wait the time shown below, then run the agent again in **terminal 2**:

```bash
python -m kusto_monitor.agent --once
```

| Incident | Wait | Expected alerts |
|---|---|---|
| `error-spike` | ~1 min (anomaly: ~2 min) | 🔴 `high-error-rate` (payments ≈ 40%) + ⚠️ `error-count-anomaly` |
| `latency` | ~1 min | ⚠️ `high-latency-p95` (checkout ≈ 1700 ms) |
| `outage` | 3+ min | 🔴 `service-silent` (auth, MinutesSilent ≥ 3) |

Example output for `error-spike`:

```
[ALERT] high-error-rate              critical rows=1     17 ms
[OK   ] high-latency-p95             warning  rows=0     15 ms
[OK   ] service-silent               critical rows=0     12 ms
[ALERT] error-count-anomaly          warning  rows=4    122 ms

🔴 [CRITICAL] high-error-rate: 1 finding(s)
A service returned more than 5% HTTP 5xx in the last 5 minutes.
Service=payments, Requests=270, Errors=108, ErrorRatePct=40.0

⚠️ [WARNING] error-count-anomaly: 4 finding(s)
Minute=2026-10-01T14:11:00+00:00, Errors=24, ExpectedErrors=6.6, Score=6.15
...
```

**Recovery:** restart `simulate live` without `--incident`. The alert clears once the bad minutes leave the
5-minute window (up to 5 min).

> **No time to wait?** Use the deterministic version, which writes history with the incident already in the last 10 minutes:
> ```bash
> python -m kusto_monitor.simulate reset
> python -m kusto_monitor.simulate backfill --hours 3 --incident error-spike --service payments
> python -m kusto_monitor.agent --once --fail-on-alert; echo "exit code: $?"    # -> 2
> ```
> Or run the whole guided 4-act show: `./scripts/demo-local.sh` (press Enter between acts).

> Stopping the simulator completely also works as a demo: after 3 minutes **every** service shows as `service-silent`.

---

## Step 8: Send alerts to Teams and keep history in Kusto

### 8a. Microsoft Teams (Workflows webhook)

1. In Teams, open the channel and choose **⋯ → Workflows**. Pick the template **"Send webhook alerts to a channel"**.
2. Name it, choose the team and channel, then **Add workflow**. **Copy the URL** it shows.
3. Test it:

```bash
export WEBHOOK_URL="<the workflow URL>"
python -m kusto_monitor.agent --once --webhook-url "$WEBHOOK_URL" --webhook-format teams
```

The agent posts an **Adaptive Card** with one block per alerting check. It posts nothing when everything is OK,
so you don't get spammed.

> **Slack:** create an *Incoming Webhook* and use `--webhook-format slack`.
> **Anything else** (Logic Apps, PagerDuty bridge, your API): `--webhook-format generic` sends structured JSON.

### 8b. Alert history in Kusto

```bash
python -m kusto_monitor.agent --once --record-alerts
```

Then query the history in the ADX web UI (queries 5–6 in `kql/demo-queries.kql`):

```kql
MonitorAlerts
| order by Timestamp desc
| project Timestamp, Severity, Check, RowCount, Details
```

---

## Step 9: Deploy the agent to Azure (Container Apps Job)

This needs a **Path A** cluster. The script does the following:

| Sub-step | What happens |
|---|---|
| 9a | creates an **Azure Container Registry** and builds the image **in Azure** with `az acr build` (no local Docker needed) |
| 9b | creates a **user-assigned managed identity** |
| 9c | grants it **AcrPull** on the registry, and **Viewer + Ingestor** on `MonitoringDb` (query, plus write alert history) |
| 9d | creates a **Container Apps environment**, which comes with Log Analytics |
| 9e | creates a **scheduled job** (`*/5 * * * *`) running `python -m kusto_monitor.agent --once` with `RECORD_ALERTS=true` |

```bash
source infra/env.sh
# optional: export WEBHOOK_URL="<teams workflow url>"   (stored as a Container Apps *secret*)
./infra/02-deploy-agent.sh
```

How the job authenticates: the container gets `KUSTO_AUTH=default` and `AZURE_CLIENT_ID=<identity client id>`.
`DefaultAzureCredential` picks up the managed identity, so **no keys or passwords exist anywhere**.

**Redeploying after you change `checks.yaml` or the code:** bump `IMAGE_TAG` in `infra/env.sh` (for example to `v2`) and rerun
`./infra/02-deploy-agent.sh`. It updates the existing job.

---

## Step 10: Test the deployed agent end to end

Keep the simulator running from your laptop (terminal 1). It writes to the Azure cluster with your `az login`:

```bash
python -m kusto_monitor.simulate live --incident error-spike --service payments
```

Wait about a minute, then trigger the job right away instead of waiting for the schedule:

```bash
source infra/env.sh
az containerapp job start -n "$JOB_NAME" -g "$RG"
az containerapp job execution list -n "$JOB_NAME" -g "$RG" -o table     # wait for Status = Succeeded (~1 min)
```

Check all three outputs:

| Where | How | Expect |
|---|---|---|
| **Job logs** | `./infra/logs.sh` (Log Analytics, allow 2–5 min delay), or Portal → job → *Execution history* → *Console logs* | the same `[ALERT] high-error-rate …` report as locally |
| **Kusto** | `MonitorAlerts \| order by Timestamp desc` in the web UI | a new row for `high-error-rate` (and `error-count-anomaly`) |
| **Teams** | the channel from Step 8 | an Adaptive Card with the alert |

Then switch the simulator back to healthy traffic. The next scheduled run (every 5 min) should report
`0 alert(s)`. **That's the full loop: data, detection, notification, recovery, all running in Azure.**

<details><summary>Checklist: "is my deployment healthy?"</summary>

```bash
az containerapp job show -n "$JOB_NAME" -g "$RG" \
  --query "{cron:properties.configuration.scheduleTriggerConfig.cronExpression, image:properties.template.containers[0].image}" -o table
az containerapp job execution list -n "$JOB_NAME" -g "$RG" --query "[0:5].{name:name,status:properties.status,start:properties.startTime}" -o table
az kusto database-principal-assignment list --cluster-name "$ADX_CLUSTER" --database-name "$KUSTO_DATABASE" -g "$RG" -o table
```
</details>

---

## Step 11: Automated tests

| Suite | Needs | Command | Covers |
|---|---|---|---|
| Unit (20 tests) | nothing | `pytest -q` | YAML validation, OK/alert/error logic, exit codes, Teams/Slack payloads, alert-history command, simulator input validation (no KQL injection) |
| End-to-end (5 tests) | Kusto emulator | see below | **real KQL** for every shipped check: healthy → no alerts; each incident → exactly the expected checks; alert history round-trip |

```bash
./scripts/start-emulator.sh
KUSTO_EMULATOR_URL=http://localhost:8080 pytest -v
# 25 passed
```

The end-to-end tests use their own database (`MonitoringDbTest`), so they won't touch your demo data.
Run them after **every change to `checks.yaml`**. They tell you whether a query still parses and still
fires on the scenario it is meant to catch.

---

## Step 12: Cost control and cleanup

```bash
source infra/env.sh

# Pause between demos (the cluster is what costs money; the job costs ~nothing):
az kusto cluster stop  --cluster-name "$ADX_CLUSTER" -g "$RG"
az containerapp job update -n "$JOB_NAME" -g "$RG" --cron-expression "0 0 1 1 *"   # effectively off while the cluster is stopped
# Resume:
az kusto cluster start --cluster-name "$ADX_CLUSTER" -g "$RG"
az containerapp job update -n "$JOB_NAME" -g "$RG" --cron-expression "*/5 * * * *"

# Wipe demo data but keep everything:
python -m kusto_monitor.simulate reset

# Delete EVERYTHING (asks you to type the resource-group name):
./infra/99-cleanup.sh

# Local emulator:
docker rm -f kusto-emulator
```

---

## 10-minute demo script

Before the demo, have a healthy `simulate live` running for at least 10 minutes, the ADX web UI open with
`kql/demo-queries.kql`, and the Teams channel visible.

| Min | Say | Do |
|---|---|---|
| 0–1 | "Our services log every request to Azure Data Explorer." | Web UI: query 1 (traffic timechart). |
| 1–3 | "The agent is just KQL: each check returns rows only when something is wrong." | Open `checks.yaml`. Paste `high-error-rate` into the web UI: 0 rows. |
| 3–4 | "It runs every 5 minutes in Azure, with a managed identity and no secrets." | Portal: Container Apps Job → Execution history (green runs). |
| 4–5 | "Let's break payments." | Terminal: `simulate live --incident error-spike --service payments` |
| 5–6 | "The error rate is climbing…" | Web UI: query 2 (error-rate timechart, payments line jumps). |
| 6–7 | "Don't wait for the schedule, run it now." | `az containerapp job start …` |
| 7–8 | "Here's the alert." | Teams card, then `MonitorAlerts` in the web UI, then `./infra/logs.sh` |
| 8–9 | "It even learns what normal looks like." | Web UI: query 4 (anomaly chart with flagged points). |
| 9–10 | "Fix it, and it recovers by itself." | Restart the simulator without `--incident`. Show the next run is green. |

Backup plan if the network or Azure misbehaves: run `./scripts/demo-local.sh` against the emulator. It's the same agent and the same checks.

---

## Add your own check

1. Write the query in the ADX web UI until it returns **only problem rows**.
2. Add it to `checks.yaml`:

   ```yaml
     - name: slow-region
       severity: warning
       description: A region's average latency is above 500 ms.
       query: |
         AppTelemetry
         | where Timestamp > ago(10m)
         | summarize AvgMs = avg(DurationMs) by Region
         | where AvgMs > 500
   ```
3. Try it: `python -m kusto_monitor.agent --once --check slow-region`
4. Add a scenario to `tests/test_integration_emulator.py` and run the tests.
5. Redeploy: bump `IMAGE_TAG` and run `./infra/02-deploy-agent.sh`.

To point the agent at **your real telemetry** (App Insights/Log Analytics exports, IoT, logs), keep the same
contract and swap `AppTelemetry` for your table names.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `KUSTO_CLUSTER_URL is not set` | Export the `KUSTO_*` variables in this terminal (Step 3). |
| `Principal 'aaduser=…' is not authorized` | You lack rights on the DB. Rerun `./infra/01-provision-adx.sh` (adds you as Admin), or in the portal open DB → *Permissions* → Add → *Admin*. |
| Job runs but logs show `not authorized` / 401 / 403 | The managed identity's Kusto role is missing or still propagating. Rerun `./infra/02-deploy-agent.sh` and wait 2–3 min. Check `AZURE_CLIENT_ID` is set on the job. |
| Job execution stuck in *Processing* / image pull error | AcrPull propagation can take a few minutes after the first deploy. Start the job again. |
| `ERROR … failed to run: Semantic error …` (exit code 3) | A query in `checks.yaml` is invalid. Paste it into the web UI to see the error. |
| Every service shows `service-silent` | The simulator isn't running. Start `simulate live`. |
| Incident injected but no alert | Wait ~1 min (3 min for `outage`). The checks look at 5-minute windows with minimum volumes. |
| `az kusto` command not found | `az extension add --name kusto` |
| `az containerapp job` not found | `az extension add --name containerapp --upgrade` |
| `.set-or-append … not authorized` on free cluster | Run the simulator as the account that created the free cluster. |
| Emulator never becomes ready | Give Docker ≥ 4 GB RAM, then check `docker logs kusto-emulator`. |

---

## What to build next

- **More sources:** export App Insights / Log Analytics to ADX, or query them directly with `cluster('…').database('…')`.
- **Smarter alerting:** suppress repeats (read the last `MonitorAlerts` row per check before notifying), and add escalation by severity.
- **Dashboards:** pin the `kql/demo-queries.kql` charts to an **ADX Dashboard** next to the alert history.
- **AI triage:** pass the alert rows plus a few context queries to an LLM to draft a likely-cause summary for the Teams card.
- **IaC:** convert `infra/*.sh` to Bicep or Terraform for repeatable environments.
