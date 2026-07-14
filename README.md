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
