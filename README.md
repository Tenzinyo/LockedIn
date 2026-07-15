# LockedIn — Bank Fraud Detection System

A 100% free, private, offline-first fraud detection pipeline. No paid APIs, no cloud
dependencies — everything runs on your local machine.

## Milestone 1: Local Infrastructure Setup

### Purpose

This milestone stands up the two pieces of infrastructure every later milestone depends
on:

1. **PostgreSQL** — stores transactions, customer profiles, audit logs, and alerts.
   Runs in Docker so it's isolated, disposable, and easy to reset.
2. **Ollama + Llama 3.1 8B** — the local LLM that powers the fraud-reasoning agent in
   Milestone 6. Runs as a native background service (not in Docker) and exposes an
   HTTP API on `localhost:11434`.

A Python virtual environment (`venv/`) isolates all project dependencies
(FastAPI, SQLAlchemy, scikit-learn, pandas, the `ollama` client, etc.) from your system
Python.

> **Note on Docker Desktop:** this machine doesn't have Docker Desktop installed.
> Instead we use **[Colima](https://github.com/abiodun/colima)** — a free, open-source
> alternative that runs the Docker daemon inside a lightweight Linux VM in the
> background. The `docker` CLI works identically either way.

> **Note on port 5433, not 5432:** this machine already runs a native Homebrew
> `postgresql@16` service bound to `127.0.0.1:5432` (check with
> `brew services list` / `lsof -nP -iTCP:5432 -sTCP:LISTEN`). That silently intercepts
> any connection to `localhost:5432`, even though the Docker container looks healthy —
> so `docker-compose.yml` maps the container to host port **5433** instead
> (`"5433:5432"`), and `config.py`'s `DB_PORT` defaults to `5433` to match. If you ever
> see `role "fraud_admin" does not exist` when connecting from Python but
> `docker exec fraud_postgres psql ...` works fine, this port conflict is why —
> `docker exec` talks to the container directly and bypasses host networking entirely,
> which is why that check alone doesn't catch it.

### Architecture Diagram

```
                    ┌─────────────────────────┐
                    │   Your Mac (macOS)      │
                    │                         │
  colima start      │  ┌───────────────────┐  │
  ───────────────►  │  │  Colima VM        │  │
                    │  │  ┌─────────────┐  │  │
                    │  │  │ dockerd     │  │  │
                    │  │  │  └─ postgres│◄─┼──┼── docker-compose.yml
                    │  │  │  host:5433  │  │  │   (fraud_detection DB)
                    │  │  │  ctr :5432  │  │  │   (5433 avoids clash with the
                    │  │  └─────────────┘  │  │    native Homebrew postgresql@16
                    │  └───────────────────┘  │    already on :5432)
                    │                         │
                    │  ┌───────────────────┐  │
                    │  │ ollama serve      │  │
                    │  │  └─ llama3.1:8b   │◄─┼──── native background service
                    │  │     :11434        │  │     (installed via ollama.com)
                    │  └───────────────────┘  │
                    │                         │
                    │  ┌───────────────────┐  │
                    │  │ venv/ (Python 3.11)│◄─┼──── requirements.txt
                    │  │  fastapi, sklearn, │  │     (app code, later milestones)
                    │  │  asyncpg, ollama…  │  │
                    │  └───────────────────┘  │
                    └─────────────────────────┘
```

### Usage / Testing Commands

**Start Docker (via Colima) and Postgres:**
```bash
colima start                 # boots the Docker VM (first run downloads a small image)
docker compose up -d         # starts the fraud_postgres container
docker ps                    # confirm it's "healthy"
```

**Verify Postgres is accepting connections (inside the container):**
```bash
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c "SELECT 1;"
```

**Verify Postgres is reachable from the host on port 5433** (this is the check that
actually catches the port-conflict issue above, since it goes through the same
networking path your Python code uses — `docker exec` alone does not):
```bash
./venv/bin/python3 -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, user='fraud_admin', password='fraud_local_dev', dbname='fraud_detection')
print(conn.cursor().execute('SELECT 1') or 'Connected on host port 5433 OK')
"
```

**Confirm Ollama is running and the model is pulled:**
```bash
ollama list                  # should show llama3.1:latest
curl -s http://localhost:11434/api/version
```

**Set up the Python environment (Python 3.11 required — see note below):**
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Run the full smoke test:**
```bash
./venv/bin/python3 -c "
import fastapi, sqlalchemy, asyncpg, psycopg2, sklearn, pandas, numpy, ollama, httpx
print('All core imports OK')
"
./venv/bin/python3 -c "
import ollama
client = ollama.Client(host='http://localhost:11434')
print('Ollama reachable, models:', [m.model for m in client.list().models])
"
```

### Why Python 3.11, not 3.14

The venv was originally created with Python 3.14 (the newest available), but two pinned
dependencies fail to build on it:
- `pydantic-core==2.27.2` — its Rust build tool (PyO3 0.22.6) only supports up to
  Python 3.13.
- `asyncpg==0.30.0` — no prebuilt wheel yet for 3.14, and it fails to build from source.

Both have prebuilt wheels for Python 3.11, so the venv uses `python3.11` (already
available via Homebrew: `/opt/homebrew/bin/python3.11`). If you rebuild the venv from
scratch, use 3.11, not whatever `python3` resolves to by default.

### Integration Points

- `docker-compose.yml` defines the `fraud_postgres` container that Milestone 2's
  `db/models.py` connects to (via `asyncpg`/SQLAlchemy) using the credentials
  `fraud_admin` / `fraud_local_dev` / `fraud_detection` on `localhost:5433`.
- The Ollama server started here is what Milestone 6's `agents/llm_agent.py` calls
  over HTTP (`localhost:11434`) via the `ollama` Python package.
- `requirements.txt` is the single source of truth for Python dependencies across
  every later milestone (config, DB models, agents, pipeline). Whenever a new
  milestone adds a dependency, it gets pinned here and reinstalled with
  `pip install -r requirements.txt`.

---

## Milestone 2: Configuration & Database Schemas

### Purpose

Two files, one goal: give every later milestone a single, consistent way to read
settings and talk to the database.

- **`config.py`** — every threshold, weight, and connection setting used anywhere in
  the system lives here (loaded from a local `.env` file if present, otherwise sane
  local-dev defaults). No other file should hardcode a magic number.
- **`db/models.py`** — SQLAlchemy 2.0 async models for the four core tables, plus the
  async engine/session factory every later milestone imports to talk to Postgres.

### The four tables

| Table | Purpose |
|---|---|
| `customer_profiles` | Baseline behavior per customer (avg transaction amount, usual active hours, account age, prior flags) — used to enrich incoming transactions in Layer 1 and to evaluate rules in Layer 2. |
| `transactions` | Every transaction event, enriched and scored as it moves through the pipeline (`rule_score`, `anomaly_score`, `final_score`, `action` get filled in by later milestones). |
| `audit_log` | One row per transaction per pipeline run — the compliance trail. Records which rules fired, the ML anomaly score, whether the LLM was invoked, and the final routing decision. |
| `alerts` | Created whenever a transaction routes to `analyst_queue` or `high_alert`. Tracks review status (`open` / `reviewed` / `dismissed`) for the Milestone 8 dashboard. |

### Architecture Diagram

```
config.py                          db/models.py
──────────                          ────────────
.env (optional overrides)           Base (DeclarativeBase)
   │                                    │
   ▼                                    ▼
Settings class ────imported by────► CustomerProfile ──┐
 - DATABASE_URL                     Transaction ───────┼──► engine (create_async_engine)
 - rule/ML/LLM thresholds           AuditLog            │        │
 - aggregator weights               Alert ──────────────┘        ▼
 - merchant risk lookup                                    async_session (async_sessionmaker)
                                                                   │
                                                                   ▼
                                                     imported by agents/, pipeline/,
                                                     main.py in later milestones
```

Running `python -m db.models` directly calls `init_models()`, which opens the async
engine and issues `CREATE TABLE IF NOT EXISTS` for all four tables via
`Base.metadata.create_all`.

### Usage / Testing Commands

**Test `config.py` loads correctly:**
```bash
./venv/bin/python3 -c "
from config import settings
print(settings.DATABASE_URL)
print('weights sum (rule+ml, llm added conditionally):', settings.RULE_WEIGHT + settings.ML_WEIGHT)
"
```

**Create the tables in Postgres** (Postgres must be running — see Milestone 1):
```bash
./venv/bin/python3 -m db.models
```
Expected output: `Tables created: customer_profiles, transactions, audit_log, alerts`

**Verify the tables exist and inspect their structure:**
```bash
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c "\dt"
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c "\d transactions"
```

### Issues hit while building this milestone (and fixes)

1. **`ModuleNotFoundError: No module named 'config'`** when running
   `python db/models.py` directly — running a file inside `db/` puts `db/` on
   `sys.path`, not the project root, so the sibling `config.py` import fails.
   **Fix:** always run it as a module from the project root: `python -m db.models`.
2. **`ValueError: the greenlet library is required`** — SQLAlchemy's async engine
   needs `greenlet`, but `requirements.txt` pinned plain `sqlalchemy` rather than the
   `sqlalchemy[asyncio]` extra, so it was never installed.
   **Fix:** added `greenlet==3.1.1` to `requirements.txt` explicitly.
3. **`asyncpg.exceptions.InvalidAuthorizationSpecificationError: role "fraud_admin" does not exist`**
   — this was the port 5432/5433 conflict described in Milestone 1, not an auth bug.
   **Fix:** already covered above — use port 5433 everywhere.

### Integration Points

- `config.settings` is imported by `db/models.py` (for `DATABASE_URL`, `CURRENCY`) and
  will be imported by every agent and `main.py` in later milestones for their
  thresholds — never hardcode a number that already has a home in `config.py`.
- `db.models.engine` / `db.models.async_session` are the async DB handles every later
  milestone reuses: Milestone 3's `generate_data.py` writes synthetic rows through
  them, Milestone 7's `main.py` reads/writes transactions through them, and
  Milestone 8's dashboard reads alerts through them (via the API, not directly).
- `Transaction.is_fraud` is a ground-truth label for synthetic data generation and
  model evaluation only — it must never be passed into the rules/ML/LLM agents at
  inference time, since real transactions won't have it.

---

## Milestone 3: Bhutanese Synthetic Data Generation

### Purpose

There's no real bank data to work with (and this project never touches real data
anyway — 100% local/private). `scripts/generate_data.py` manufactures a realistic
stand-in so every later milestone has something to train and test against:

- **500 customer profiles** — Bhutanese names, spending baselines
  (`avg_txn_amount`), and usual active hours per customer.
- **12,000 transactions** — BTN currency, real Bhutan-region merchants
  (TashiCell, B-Mobile, Thimphu Centenary Farmers Market, Druk Jewellers, etc.),
  weighted so everyday categories (grocery, telecom, retail) dominate and
  high-risk ones (crypto, jewelry, gaming) stay rare.
- **~2% injected fraud** — the fraud rate reuses `settings.ML_CONTAMINATION`
  (already defined in Milestone 2) rather than a new hardcoded constant, since
  that's the same 2% the Isolation Forest in Milestone 4 will be tuned for.
  Fraud rows are pushed to look anomalous on purpose: 5-20x the customer's
  normal amount, odd hours (1-4am), new payees, foreign IP countries, and — for
  ~30% of fraud cases — rapid-fire bursts of 3-5 transactions within the
  velocity window (`settings.VELOCITY_WINDOW_MINUTES`), so Milestone 5's
  velocity rule has real bursts to catch.

### Architecture Diagram

```
scripts/generate_data.py
─────────────────────────
build_customers()  ──► 500 CustomerProfile rows ──► async_session ──► Postgres
        │                                                                │
        ▼                                                                │
build_transactions()                                                     │
  - picks category/merchant per weighted probabilities                  │
  - normal txns: lognormal around customer's avg_txn_amount             │
  - fraud txns (~2%, via settings.ML_CONTAMINATION):                    │
      amount x5-20, night hours, new payee, foreign IP,                 │
      30% as 3-5 txn velocity bursts                                    │
        │                                                                │
        ▼                                                                │
  12,000 Transaction rows ──► batched async_session.add_all() ──────────►┘
  (1,000 rows/batch, committed per batch)
```

### Usage / Testing Commands

**Generate the dataset** (clears any previously generated rows first, so it's
safe to re-run — Postgres must be running, see Milestone 1):
```bash
./venv/bin/python3 -m scripts.generate_data
```
Expected output:
```
Customers created: 500
Transactions created: 12000
Fraud transactions: 240 (2.00%)
```

**Spot-check the data:**
```bash
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c \
  "SELECT is_fraud, count(*), round(avg(amount)::numeric,2) FROM transactions GROUP BY is_fraud;"
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c \
  "SELECT merchant_category, count(*) FROM transactions GROUP BY merchant_category ORDER BY 2 DESC;"
```
Fraud rows should average noticeably higher amounts than non-fraud rows — if
they look statistically identical, Milestone 4's model will have nothing to
learn from.

### Issues hit while building this milestone (and fixes)

1. **Fraud rate drifted to 3.55% instead of the target 2%.** The first version
   picked `is_fraud` per-transaction from a pre-shuffled flag array, but
   velocity-burst fraud events generate 3-5 rows per event — so consuming
   multiple array slots per event desynced the flag index from the actual row
   count. **Fix:** build a list of "events" up front (single row or 3-5 row
   burst) sized to hit the exact fraud-row budget, then top up with single-row
   normal events to reach 12,000 total, and shuffle the event list — not
   individual rows.
2. **`merchant_name` was computed but had nowhere to go.** The generator picked
   a specific merchant (e.g. "TashiCell") per transaction for realism, but
   `Transaction` only had `merchant_category` — the name was being discarded.
   **Fix:** added a `merchant_name` column to `Transaction` in `db/models.py`
   (Milestone 2) and wired it through; tables were dropped and recreated to
   pick up the new column.

### Integration Points

- Reuses `settings.ML_CONTAMINATION` (Milestone 2) as the fraud injection rate
  and `settings.VELOCITY_WINDOW_MINUTES` / `NIGHT_HOUR_START` / `NIGHT_HOUR_END`
  to shape fraud patterns — no new magic numbers introduced for these.
- Writes through `db.models.async_session` / `engine`, the same handles
  Milestone 7's `main.py` and Milestone 8's dashboard will use.
- Milestone 4's `train_model.py` reads these transactions (features derived
  from `amount`, `channel`, `transaction_time`, etc.) to train the Isolation
  Forest, using `is_fraud` only to evaluate the trained model — never as a
  training feature.
