# No-Code Kusto Monitoring Agent: Azure Portal + Logic Apps

This guide builds the same monitoring agent as [`../kusto-monitor`](../kusto-monitor/README.md) **without writing
or running any code**. Everything happens in your browser at **portal.azure.com**. The only typing is pasting
ready-made Kusto (KQL) text from this folder.

| Piece | Built with | What it does |
|---|---|---|
| Data store | **Azure Data Explorer** (Kusto) | holds app telemetry and alert history |
| Health checks | **Kusto stored functions** (one paste) | `RunAllChecks()` returns one row per problem, or nothing when healthy |
| Demo traffic | **Logic App #1: Traffic Generator** | every minute, writes fake traffic for 4 services, with an optional injected incident |
| The agent | **Logic App #2: Monitor Agent** | every 5 minutes, runs the checks. If anything is wrong it logs the alert, posts to **Teams** and sends an **email** |

```
 ┌───────────────────────────┐ every 1 min  ┌─────────────────────────────────────────┐
 │ Logic App #1              │ ───────────▶ │ Azure Data Explorer · MonitoringDb      │
 │ Traffic Generator         │              │  AppTelemetry      SimulatorSettings ◀──┼── you flip incidents
 └───────────────────────────┘              │  AlertLog          functions: RunAllChecks() …
                                            └───────────▲───────────────┬─────────────┘
                                         RunAllChecks() │               │ .set-or-append AlertLog
                                            ┌───────────┴───────────────▼─────────────┐
                                            │ Logic App #2 · Monitor Agent (every 5m) │──▶ Teams channel
                                            │ rows > 0 ?  → log, table, notify        │──▶ Email
                                            └─────────────────────────────────────────┘
```

| | |
|---|---|
| **Time** | ~45 min, about 15 of which is waiting for the cluster |
| **Cost** | The ADX *Dev (No SLA)* cluster is billed per hour while running. The two Logic Apps (Consumption plan) cost well under $1/day at these schedules. **Stop everything after the demo** ([Step 10](#step-10--stop-or-delete-everything)). |
| **You need** | An Azure subscription where you can create resources (Contributor or Owner). For Teams alerts you need a **work or school** Microsoft 365 account. Email works with Office 365 or Outlook.com. |
| **Files you'll paste from** | [`setup.kql`](setup.kql) (once) and [`demo-commands.kql`](demo-commands.kql) (during the demo) |

---

## Contents

- [Step 1: Create a resource group](#step-1--create-a-resource-group)
- [Step 2: Create the Azure Data Explorer cluster and database](#step-2--create-the-azure-data-explorer-cluster-and-database)
- [Step 3: Paste the setup script (tables and checks)](#step-3--paste-the-setup-script-tables--checks)
- [Step 4: Load 6 hours of history and look at it](#step-4--load-6-hours-of-history-and-look-at-it)
- [Step 5: Logic App #1, the Traffic Generator](#step-5--logic-app-1--traffic-generator)
- [Step 6: Logic App #2, the Monitor Agent](#step-6--logic-app-2--monitor-agent)
- [Step 7: Test it: the four scenarios](#step-7--test-it-the-four-scenarios)
- [Step 8 (optional): A live dashboard](#step-8-optional--a-live-dashboard)
- [Step 9: 10-minute demo script](#step-9--10-minute-demo-script)
- [Step 10: Stop or delete everything](#step-10--stop-or-delete-everything)
- [Change a threshold or add a check (still no code)](#change-a-threshold-or-add-a-check-still-no-code)
- [Troubleshooting](#troubleshooting)

---

## Step 1: Create a resource group

1. Open <https://portal.azure.com>. In the top search bar type **Resource groups** and open it.
2. Click **+ Create**.
3. Fill in:
   - **Subscription**: yours
   - **Resource group**: `rg-kusto-monitor-demo`
   - **Region**: e.g. `East US` (use the **same region** for everything below)
4. Click **Review + create**, then **Create**.

✅ **Check:** the resource group appears in the list.

---

## Step 2: Create the Azure Data Explorer cluster and database

### 2a. Cluster

1. Search for **Azure Data Explorer** and open it, then click **+ Create**.
2. **Basics** tab:
   - **Resource group**: `rg-kusto-monitor-demo`
   - **Cluster name**: something globally unique, lowercase letters and digits only, e.g. `adxmonitor<yourinitials>01`
   - **Region**: same as Step 1
   - **Workload**: **Dev/test**. This selects the cheapest *Dev (No SLA)* size. Leave the compute size as suggested.
   - **Availability zones**: none / off
3. Click **Review + create**, then **Create**. ⏳ **This takes about 10–20 minutes.** Read ahead meanwhile.
4. When it finishes, click **Go to resource**. On **Overview**, copy the **URI**, e.g.
   `https://adxmonitorjk01.eastus.kusto.windows.net`. **You will paste this into both Logic Apps.**

### 2b. Database

1. In the cluster, open **Data → Databases** in the left menu and click **+ Add database**.
2. **Database name**: `MonitoringDb` · **Retention period**: `30` days · **Cache period**: `7` days, then **Create**.

### 2c. Check your permissions

1. Open the database **MonitoringDb**, then **Permissions** in the left menu.
2. You should already be listed (the creator gets admin rights). If not, click **+ Add → Admin** and pick yourself.

✅ **Check:** `MonitoringDb` shows in **Databases** with status *Online*.

---

## Step 3: Paste the setup script (tables and checks)

1. In the cluster's left menu, click **Query**. The Kusto query editor opens inside the portal.
2. In the tree on the left, click **MonitoringDb** so it's the selected database.
3. Open [`setup.kql`](setup.kql), copy **the whole file**, and paste it into the editor.
4. Press **Ctrl+A** (select all), then click **Run**.

   > ⚠️ Select all first. Without a selection, the editor runs only the block your cursor is in (blocks are separated by blank lines).

5. The result grid shows **11 rows, all with `Result = Completed`**.

What it created (expand **MonitoringDb** in the tree, then refresh):

| Object | Type | Purpose |
|---|---|---|
| `AppTelemetry` | table | one row per HTTP request (time, service, region, status code, latency) |
| `SimulatorSettings` | table | the "incident switch" the Traffic Generator reads |
| `AlertLog` | table | every alert the agent raised |
| `GenerateTraffic()`, `NextTrafficBatch()` | functions (folder *Simulator*) | build fake traffic |
| `CheckHighErrorRate()` | function (*Checks*) | 🔴 a service with > 5% HTTP 5xx in the last 5 min |
| `CheckHighLatency()` | function (*Checks*) | ⚠️ a service whose 95th-percentile latency is > 800 ms over 5 min |
| `CheckServiceSilent()` | function (*Checks*) | 🔴 a service reported in the last hour but not in the last 3 min |
| `CheckErrorAnomaly()` | function (*Checks*) | ⚠️ errors per minute well above a baseline learned from the last 3 hours (`series_decompose_anomalies`) |
| `RunAllChecks()` | function (*Checks*) | all four combined. **This is the only thing the agent calls.** |

> Re-running `setup.kql` later is safe. It updates the functions, keeps your data, and resets the incident switch to *healthy*.

---

## Step 4: Load 6 hours of history and look at it

Still in **Query**, paste and **Run** this one line (it's block 1 in [`demo-commands.kql`](demo-commands.kql)):

```kql
.set-or-append AppTelemetry <| GenerateTraffic(ago(6h), now(), "none", "payments")
```

It writes about 86,000 rows of healthy traffic for four services: `checkout`, `payments`, `catalog`, `auth`.
The anomaly check needs this history to learn what "normal" looks like.

Now look at it. Paste and run:

```kql
AppTelemetry
| where Timestamp > ago(1h)
| summarize Requests = count() by Service, bin(Timestamp, 1m)
| render timechart
```

✅ **Check:** four flat lines at about 60 requests per minute. Then run `RunAllChecks()`, which should return **0 rows**, meaning everything is healthy.

---

## Step 5: Logic App #1, the Traffic Generator

This Logic App plays the role of "your application", writing fresh telemetry every minute.

### 5a. Create it

1. Search for **Logic apps** and click **+ Add**.
2. **Plan type**: **Consumption** (multi-tenant, pay per run).
3. **Resource group**: `rg-kusto-monitor-demo` · **Name**: `la-kusto-traffic-generator` · **Region**: same as before.
4. **Review + create**, then **Create**, then **Go to resource**.

### 5b. Design it

1. Left menu: **Development Tools → Logic app designer**.
2. **Add a trigger**: search **Recurrence** (Schedule). Set **Interval** `1`, **Frequency** `Minute`.
3. Click **+ → Add an action**. Search **Azure Data Explorer** and choose
   **Run control command and visualize results**.
4. **Create a connection**: give it a name like `adx-demo`, keep **OAuth** (sign in), click **Sign in**, and use your account.
5. Fill the action:
   - **Cluster URL**: the URI from Step 2a
   - **Database Name**: `MonitoringDb`
   - **Control Command**:
     ```kql
     .set-or-append AppTelemetry <| NextTrafficBatch()
     ```
   - **Chart Type**: `Html Table`
6. Click **Save** (top left).

`NextTrafficBatch()` continues from the newest row in the table, so there are no gaps or duplicates. It also reads
the newest row of `SimulatorSettings` to decide whether to inject an incident.

### 5c. Test it

1. Click **Run → Run** on the designer toolbar, then open **Overview → Runs history**.
2. The run shows **Succeeded**, and it keeps running every minute from now on.

✅ **Check:** in **Query**, run `AppTelemetry | summarize max(Timestamp)`. The value is within the last minute and moves forward each minute.

---

## Step 6: Logic App #2, the Monitor Agent

### 6a. Create it

Same as 5a, but with **Name** `la-kusto-monitor-agent`.

### 6b. Design it

Open **Logic app designer** and build this flow:

```
Recurrence (every 5 min)
  └─ Run KQL query: RunAllChecks()
       └─ Condition: number of rows > 0
            ├─ True:  ① Record alerts in AlertLog
            │         ② Create HTML table
            │         ③ Post message in Teams
            │         ④ Send an email
            └─ False: (nothing, all healthy)
```

**Trigger.** Choose **Recurrence**, with **Interval** `5` and **Frequency** `Minute`.

**Action: run the checks.**
1. **+ → Add an action**, then **Azure Data Explorer → Run KQL query**. It reuses the `adx-demo` connection from Step 5.
2. **Cluster URL**: your URI · **Database Name**: `MonitoringDb` · **Query**:
   ```kql
   RunAllChecks()
   ```

**Condition: did anything fail?**
1. **+ → Add an action → Control → Condition**.
2. Left box: click it, then the **fx** (expression) icon, and enter:
   ```
   length(body('Run_KQL_query')?['value'])
   ```
   Click **Add**.
3. Operator: **is greater than** · Right box: `0`.

   > If you renamed the KQL action, change `Run_KQL_query` to match (spaces become `_`).

**Inside True, add four actions in this order:**

**① Record alerts.** Choose **Azure Data Explorer → Run control command and visualize results**.
- Cluster URL / Database: as above
- **Control Command**:
  ```kql
  .set-or-append AlertLog <| RunAllChecks()
  ```
- **Chart Type**: `Html Table`

**② Create HTML table.** Choose **Data Operations → Create HTML table**.
- **From**: click the box, open the **⚡ dynamic content** list, and under *Run KQL query* pick **value**.
- **Columns**: `Automatic`

**③ Post to Teams.** Choose **Microsoft Teams → Post message in a chat or channel** and sign in with your work account.
- **Post as**: `Flow bot` · **Post in**: `Channel` · **Team** / **Channel**: pick yours
- **Message**: type `🚨 Kusto Monitoring Agent found problems:`, then insert dynamic content **Output** (from *Create HTML table*).

**④ Email.** Choose **Office 365 Outlook → Send an email (V2)**, or **Outlook.com → Send an email (V2)** for a personal account.
- **To**: your address
- **Subject**: `Kusto Monitoring Agent:` then the expression `length(body('Run_KQL_query')?['value'])`, then ` alert(s)`
- **Body**: insert dynamic content **Output** (from *Create HTML table*)

Leave the **False** branch empty. Click **Save**.

> Don't use Teams? Skip ③. Don't want email? Skip ④. The agent still records every alert in `AlertLog`.

### 6c. First test run (healthy)

1. **Run → Run**, then **Overview → Runs history**, then open the run.
2. Each step shows a ✅. **Run KQL query** has an empty `value: []`, and the **Condition** went to **False**.

✅ **Check:** no Teams message and no email, because nothing is wrong. That is correct.

---

## Step 7: Test it: the four scenarios

You switch incidents by running **one line in Query** (all of them are in [`demo-commands.kql`](demo-commands.kql), block 2).
The Traffic Generator picks up the change within a minute.

| # | Run this in Query | Wait | Then | Expected result |
|---|---|---|---|---|
| 1 | *(healthy, nothing to do)* | none | Monitor Agent: **Run → Run** | Condition **False**, no message |
| 2 | `.set-or-append SimulatorSettings <\| print Incident = "error-spike", Service = "payments", UpdatedAt = now()` | **2 min** | **Run → Run** | 🔴 `high-error-rate`: "payments: ~40% errors…" and ⚠️ `error-count-anomaly` |
| 3 | `.set-or-append SimulatorSettings <\| print Incident = "latency", Service = "checkout", UpdatedAt = now()` | **2 min** | **Run → Run** | ⚠️ `high-latency-p95`: "checkout: p95 latency ~1700 ms" (the payments alert fades within 5 min) |
| 4 | `.set-or-append SimulatorSettings <\| print Incident = "outage", Service = "auth", UpdatedAt = now()` | **4 min** | **Run → Run** | 🔴 `service-silent`: "auth: no telemetry for N min" |
| 5 | `.set-or-append SimulatorSettings <\| print Incident = "none", Service = "payments", UpdatedAt = now()` | **5 min** | **Run → Run** | back to healthy, no message |

> In the table above, `<\|` is just `<|` (escaped for Markdown). Copy the lines from `demo-commands.kql` instead.

For each alerting run, verify all three outputs:

| Where | What you see |
|---|---|
| **Logic App → Runs history → the run** | Condition = **True**. Click *Run KQL query → Show raw outputs* to see the rows. |
| **Teams channel** | a Flow bot message with a table: Timestamp · Severity · Check · Summary |
| **Email inbox** | subject `Kusto Monitoring Agent: 2 alert(s)` with the same table |
| **Query:** `AlertLog \| order by Timestamp desc` | one row per alert, kept forever as history |

💡 **Shortcut while testing:** run `RunAllChecks()` in Query at any time. It shows exactly what the agent would see right now.

---

## Step 8 (optional): A live dashboard

A no-code visual to keep on screen during the demo:

1. In the cluster, open **Query**, then **Open in Web UI** (or go to <https://dataexplorer.azure.com>), then **Dashboards → New dashboard**.
2. **+ Add data source**: your cluster URI and `MonitoringDb`.
3. **+ Add tile** three times, once for each query from [`demo-commands.kql`](demo-commands.kql) block 7 (traffic, error rate %, p95 latency). Pick visual type **Time chart**.
4. Add a fourth tile with `AlertLog | order by Timestamp desc | take 20` as a **Table**.
5. Set **Auto refresh** to 1 minute and **Save**.

---

## Step 9: 10-minute demo script

**Before the demo:** both Logic Apps have been running for at least 10 minutes. You have tabs open for Query, the Monitor Agent *Runs history*, Teams and the dashboard.

| Min | Say | Do |
|---|---|---|
| 0–1 | "Our four services stream every request into Azure Data Explorer." | Dashboard or traffic timechart |
| 1–2 | "The checks are plain Kusto functions. Zero rows means healthy." | Query: `RunAllChecks()` returns 0 rows. Expand the *Checks* folder. |
| 2–3 | "A Logic App runs them every 5 minutes. No servers, no code." | Show the Monitor Agent designer and the green runs history |
| 3–4 | "Let's break payments." | Query: the `error-spike` line |
| 4–6 | "Errors are climbing." | Error-rate timechart: the payments line jumps |
| 6–7 | "No need to wait for the schedule." | Monitor Agent: **Run → Run** |
| 7–8 | "Here's the alert." | Teams message, then email, then `AlertLog` |
| 8–9 | "It even learned what normal looks like." | Anomaly chart from `demo-commands.kql` block 7 |
| 9–10 | "Fix it, and it heals itself." | Query: the `none` line. The next scheduled run is quiet. |

---

## Step 10: Stop or delete everything

**Pause** between demos (keeps everything, stops nearly all cost):
1. Each Logic App: **Overview → Disable**.
2. ADX cluster: **Overview → Stop**. Use **Start** later; it takes a few minutes.

**Delete** everything: open **Resource groups → rg-kusto-monitor-demo → Delete resource group**, then type the name to confirm.

**Reset demo data only:** run block 8 of `demo-commands.kql` (`.clear table AppTelemetry data` and `.clear table AlertLog data`), then block 1 to reload history.

> If the cluster was stopped for a while, run block 1 again after starting it. The anomaly check needs a few hours of continuous history.

---

## Change a threshold or add a check (still no code)

Every check is a function, so you edit it in **Query**:

1. In the tree, expand **MonitoringDb → Functions → Checks**, right-click **CheckHighErrorRate**, and choose **Script function**. Its definition opens in the editor.
2. Change `ErrorRatePct > 5` to, say, `ErrorRatePct > 10` and click **Run**. The change is live at the next agent run, with no Logic App change needed.
3. **New check:** copy any check, rename it (e.g. `CheckSlowRegion`), and keep the output columns `Check`, `Severity`, `Summary`. Then add it to the `union` line inside `RunAllChecks()`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Setup script: *Admin commands must have a dot (.) character as their first non-whitespace character* | You ran only part of it, or there's text above `.execute`. Paste the file exactly, **Ctrl+A**, then Run. |
| Setup script: *Principal … is not authorized* | Database → **Permissions → + Add → Admin** → yourself (Step 2c). |
| Logic App step: **Unauthorized / 403** | The `adx-demo` connection's account lacks rights on `MonitoringDb`. Fix the permissions, or open **API connections → adx-demo → Edit API connection → Authorize**. |
| Condition errors with *body('Run_KQL_query') not found* | Your action has a different name. Use its name with spaces replaced by `_`. |
| Every service shows `service-silent` | The Traffic Generator is disabled or failing. Check its runs history. |
| Incident injected but no alert | Wait 2 min (4 min for outage) **after** the generator's next run, then run the agent. The checks look at 5-minute windows. |
| `error-count-anomaly` never fires | It needs about 3 h of history. Run block 1 (6 h backfill). |
| Teams action missing or fails | Teams needs a work or school account. Use only the email action instead. |
| Email shows raw `<table>` text | In the email action, open the Body editor's **code view (`</>`)** and insert *Output* there. |
| Generator action rejects the command | Use **Run async control command** with `.set-or-append async AppTelemetry <\| NextTrafficBatch()` instead. |
| Same alert every 5 minutes | Expected: the agent reports state on every run while the problem lasts. For quieter alerts, change the Recurrence to 15 min. |

---

## How this maps to the code version

| No-code (this guide) | Code version (`../kusto-monitor`) |
|---|---|
| `RunAllChecks()` + `Check*()` functions | `checks.yaml` |
| Logic App #2 (Recurrence + Condition) | Container Apps Job running `kusto_monitor.agent` |
| Logic App #1 + `SimulatorSettings` | `python -m kusto_monitor.simulate live --incident …` |
| Teams/Outlook connectors | `--webhook-url` (Teams/Slack) |
| `AlertLog` | `MonitorAlerts` |

The KQL in `setup.kql` is covered by the code version's automated tests (`tests/test_nocode_emulator.py`),
which run the exact same script against a real Kusto engine.
