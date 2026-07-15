# LockedIn — Bank Fraud Detection System

A 100% free, private, offline-first fraud detection pipeline. No paid APIs, no cloud
dependencies — everything runs on your local machine.

## Milestone 1: Local Infrastructure Setup

> **In short:** stands up the database and the local AI model on your machine — the
> plumbing every later milestone plugs into.
> **Analogy:** installing the plumbing and electricity before you can cook anything in
> the house.

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

> **In short:** one shared rulebook for every threshold/weight, and the database
> filing cabinets every transaction, alert, and audit record will live in.
> **Analogy:** designing the forms and house rules a bank branch will use before any
> customer ever walks in.

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

> **In short:** manufactures a realistic pretend customer base and transaction history
> (with known fraud mixed in) since there's no real bank data to train or test against.
> **Analogy:** a flight simulator — practicing on realistic but fake scenarios before
> anything touches a real plane.

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

---

## Milestone 4: Offline ML Model Training

> **In short:** teaches a model what "normal" spending looks like purely from patterns
> in the data, with no fraud labels used, so it can later flag whatever doesn't fit.
> **Analogy:** a security guard who's watched thousands of people walk through a lobby
> and now notices when someone's behavior looks off — without ever being handed a list
> of specific rules to check.

### Purpose

`scripts/train_model.py` turns Milestone 3's synthetic transactions into a
trained anomaly-detection model that Milestone 5's `agents/ml_agent.py` will
load at inference time.

- Pulls every transaction + its customer's baseline (`avg_txn_amount`) out of
  Postgres.
- Engineers the exact six features the ML agent will compute later:
  `amount_to_avg_ratio`, `hour`, `day_of_week`, `txn_count_60min`,
  `is_new_payee`, `channel_encoded`.
- Fits a scikit-learn `IsolationForest` (`contamination=settings.ML_CONTAMINATION`,
  the same 2% used to generate the data) — fully unsupervised, `is_fraud` is
  never fed in as a feature.
- Evaluates the trained model against `is_fraud` (precision/recall/confusion
  matrix) purely as a sanity check, then saves the model to
  `settings.ML_MODEL_PATH` (`models/fraud_model.pkl`) via joblib.

### Architecture Diagram

```
scripts/train_model.py
────────────────────────
load_transactions_df() ──► Postgres (transactions JOIN customer_profiles)
        │
        ▼
engineer_features()
  - amount_to_avg_ratio = amount / customer.avg_txn_amount
  - hour, day_of_week    <- transaction_time
  - txn_count_60min      <- two-pointer sliding window per customer
                             (settings.ML_TXN_COUNT_WINDOW_MINUTES)
  - channel_encoded      <- settings.CHANNEL_ENCODING (shared with
                             agents/ml_agent.py so train/inference never drift)
        │
        ▼
IsolationForest(contamination=settings.ML_CONTAMINATION,
                random_state=settings.ML_RANDOM_STATE)
        │
        ├──► evaluate against is_fraud (ground truth, sanity check only)
        │
        ▼
joblib.dump ──► models/fraud_model.pkl ──► loaded by agents/ml_agent.py (M5)
```

### Usage / Testing Commands

**Train the model** (Postgres must be running with Milestone 3's data
already generated):
```bash
./venv/bin/python3 -m scripts.train_model
```
Expected output (numbers will vary slightly run to run since `generate_data.py`
uses randomness, though the fraud rate is always fixed at 2%):
```
Trained on 12000 transactions, [...]
Flagged as anomalies: 240 (2.00%)
Classification report: fraud precision/recall around 0.7-0.8
Model saved to models/fraud_model.pkl
```

**Verify the saved model loads and scores a transaction:**
```bash
./venv/bin/python3 -c "
import joblib
from config import settings
model = joblib.load(settings.ML_MODEL_PATH)
# [amount_to_avg_ratio, hour, day_of_week, txn_count_60min, is_new_payee, channel_encoded]
print(model.predict([[12.0, 2, 3, 4, 1, 1]]))   # looks fraud-shaped -> expect [-1]
print(model.predict([[1.0, 14, 3, 1, 0, 0]]))   # looks normal      -> expect [1]
"
```

### Integration Points

- Reuses `settings.ML_CONTAMINATION`, `settings.ML_RANDOM_STATE`,
  `settings.ML_TXN_COUNT_WINDOW_MINUTES`, and the new `settings.CHANNEL_ENCODING`
  (Milestone 2) — no new magic numbers introduced here.
- `settings.CHANNEL_ENCODING` is the critical shared contract with Milestone 5:
  `agents/ml_agent.py` must encode `channel` the same way at inference time or
  the loaded model will silently score garbage.
- `settings.ML_MODEL_PATH` (`models/fraud_model.pkl`) is the file
  `agents/ml_agent.py` loads via `joblib.load()` — re-running
  `train_model.py` overwrites it in place.
- Must be re-run any time `scripts/generate_data.py` is re-run, since the
  underlying transactions (and therefore the model) would otherwise be stale.

---

## Milestone 5: Deterministic Rules & ML Agents

> **In short:** two independent judges score every transaction — one follows a fixed
> checklist of red flags, the other looks for statistical oddities it learned in
> Milestone 4.
> **Analogy:** having both a checklist-following inspector and an experienced,
> gut-instinct inspector examine the same shipment, independently and at the same time.

### Purpose

Two independent scoring agents, run side by side in Milestone 7's pipeline,
each producing a score in `[0, 1]`:

- **`agents/rules_agent.py`** — six deterministic checks, each adding a fixed
  weight to `rule_score` (capped at `settings.RULE_SCORE_CAP`): amount > 5x
  the customer's average, >3 transactions in a 10-minute window, IP country ≠
  home country, new payee + amount > 10,000 BTN, transaction between 1-4am,
  and IP on the local blacklist. Every threshold/weight already lived in
  `config.py` from Milestone 2 — this file only implements the checks.
- **`agents/ml_agent.py`** — loads the Isolation Forest trained in
  Milestone 4, computes the same six features for a single transaction, and
  rescales the model's raw (unbounded) anomaly score into `[0, 1]` using the
  1st/99th-percentile bounds saved alongside the model.
- **`agents/features.py`** (new) — the feature-engineering logic shared
  between `train_model.py` and `ml_agent.py`, so training and inference can
  never quietly drift apart on what a feature means.

### Architecture Diagram

```
Transaction + CustomerProfile
        │
        ├──────────────────────────────┬───────────────────────────────┐
        ▼                               ▼
agents/rules_agent.py            agents/ml_agent.py
  score_transaction()              score_transaction()
  - high_amount                    - amount_to_avg_ratio  ┐
  - velocity (DB query)            - hour, day_of_week    ├─ agents/features.py
  - ip_country_mismatch            - txn_count_60min       │  (shared w/ train_model.py)
  - new_payee_high_amount          - is_new_payee          │
  - night_hour                     - channel_encoded      ┘
  - ip_blacklist                        │
        │                               ▼
        │                     models/fraud_model.pkl (Milestone 4)
        │                       {model, score_low, score_high}
        │                               │
        ▼                               ▼
  RuleResult(rule_score,          anomaly_score
             triggered_rules)     (rescaled to [0,1])
        │                               │
        └───────────────┬───────────────┘
                         ▼
              Milestone 7's aggregator (not built yet)
```

### Usage / Testing Commands

**Test the rules agent** (prints rule_score + triggered rules for 5 known
fraud and 5 known normal transactions from Milestone 3's data):
```bash
./venv/bin/python3 -m agents.rules_agent
```
Expect fraud-sample scores well above 0 with multiple triggered rules, and
normal-sample scores at (or near) 0.

**Test the ML agent** (same sample split, prints anomaly_score):
```bash
./venv/bin/python3 -m agents.ml_agent
```
Expect fraud-sample scores noticeably higher than normal-sample scores, all
within `[0, 1]`.

### Issues hit while building this milestone (and fixes)

1. **`IP_BLACKLIST_SCORE` existed in config.py since Milestone 2 but no
   blacklist existed to check against**, so the rule could never fire.
   **Fix:** added `settings.IP_BLACKLIST` (a small static list — no external
   threat feed, everything stays offline) and updated
   `scripts/generate_data.py` to assign a blacklisted IP to ~20% of
   non-burst fraud transactions, so the rule has real data to catch.
2. **IsolationForest's `decision_function` is unbounded**, so it can't be
   used directly as a `[0, 1]` anomaly score. **Fix:** `train_model.py` now
   saves the 1st/99th percentile of training-set decision scores alongside
   the model; `ml_agent.py` linearly rescales against those bounds and clips
   to `[0, 1]`.
3. **Feature engineering was about to be duplicated** between
   `train_model.py` (pandas, batch) and `ml_agent.py` (single transaction).
   **Fix:** extracted `FEATURE_COLUMNS` and `amount_to_avg_ratio()` into
   `agents/features.py`, imported by both.

### Integration Points

- Both agents take the same inputs — `(transaction, customer, session)` —
  and will be called via `asyncio.gather()` in Milestone 7's `main.py`
  (per the architecture's Layer 2/3 design), always writing to `audit_log`
  regardless of outcome.
- `agents/rules_agent.py`'s `triggered_rules` list is exactly the JSONB shape
  `AuditLog.triggered_rules` expects.
- `agents/ml_agent.py` raises `RuntimeError` at call time (not import time)
  if `models/fraud_model.pkl` is missing, so re-running
  `scripts/train_model.py` is the fix, not a code change.
- Milestone 6's `agents/llm_agent.py` will only be invoked when
  `rule_score` or `anomaly_score` exceeds `settings.LLM_INVOKE_THRESHOLD`.

---

## Milestone 6: Local LLM Agent with Tool Calling

> **In short:** brings in a local AI investigator that actually looks things up before
> writing its verdict, but only after the two judges from Milestone 5 already raised a
> flag.
> **Analogy:** calling in a detective only after the beat cop and the alarm system both
> said something's wrong — the detective interviews witnesses (the tools) before
> writing the case report, rather than guessing from the scene alone.

### Purpose

`agents/llm_agent.py` is Layer 4 — the expensive layer, only invoked when
Layer 2 (rules) or Layer 3 (ML) already flagged a transaction, i.e.
`max(rule_score, anomaly_score) > settings.LLM_INVOKE_THRESHOLD`. It gives
Ollama's `llama3.1` three tools to investigate before writing a short
natural-language explanation for the human analyst:

- **`lookup_customer_history`** — recent transaction count, average amount,
  distinct payees, account age, and prior flag count. Deliberately excludes
  `is_fraud` — that ground-truth label must never reach any agent at
  inference time.
- **`check_ip_reputation`** — geolocation/network reputation via
  `ip-api.com`'s free keyless endpoint.
- **`get_merchant_risk`** — looks up `settings.MERCHANT_RISK_SCORES`
  (Milestone 2).

Per the aggregator design (Milestone 7), the LLM's value here is the audit
trail it leaves — `AuditLog.llm_explanation` — not a further numeric score;
invoking it contributes a flat `settings.LLM_WEIGHT` bump to `final_score`.

### Architecture Diagram

```
rule_score, anomaly_score > LLM_INVOKE_THRESHOLD?
        │ no                      │ yes
        ▼                         ▼
  skip (llm_called=False)   agents/llm_agent.py
                                   │
                          messages = [system, user_prompt]
                                   │
                                   ▼
                    ollama.AsyncClient.chat(model=llama3.1, tools=TOOLS)
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼              ▼
           lookup_customer_   check_ip_    get_merchant_risk
           history (Postgres) reputation   (config lookup)
                (ip-api.com, httpx)
                     │             │              │
                     └─────────────┴──────────────┘
                                   │  tool results appended to messages
                                   ▼
                     loop until no more tool_calls
                     (capped at LLM_MAX_TOOL_ITERATIONS)
                                   │
                                   ▼
                    final message.content -> llm_explanation
```

### Usage / Testing Commands

**Test the LLM agent** (Ollama must be running with llama3.1 pulled — see
Milestone 1; picks 2 known-fraud transactions, runs rules + ML agents first
to get real scores, then investigates):
```bash
./venv/bin/python3 -m agents.llm_agent
```
Expected: an `llm_explanation` for each transaction, referencing specifics
like the amount, merchant, IP country mismatch, or new-payee flag.

**Verify tool calls are actually happening** (not just the model reasoning
from the prompt alone) — prints each iteration's `tool_calls` and the tool's
real result:
```bash
./venv/bin/python3 -c "
import asyncio
from sqlalchemy import select
from agents.llm_agent import SYSTEM_PROMPT, TOOLS, _build_user_prompt, _execute_tool
from agents.rules_agent import score_transaction as rules_score
from agents.ml_agent import score_transaction as ml_score
from db.models import Transaction, CustomerProfile, async_session, engine
import ollama
from config import settings

async def main():
    async with async_session() as session:
        stmt = (select(Transaction, CustomerProfile)
                .join(CustomerProfile, Transaction.customer_id == CustomerProfile.customer_id)
                .where(Transaction.is_fraud.is_(True)).limit(1))
        transaction, customer = (await session.execute(stmt)).first()
        rule_result = await rules_score(transaction, customer, session)
        anomaly = await ml_score(transaction, customer, session)
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': _build_user_prompt(transaction, customer, rule_result.rule_score, anomaly, rule_result.triggered_rules)}]
        client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        for i in range(settings.LLM_MAX_TOOL_ITERATIONS):
            resp = await client.chat(model=settings.OLLAMA_MODEL, messages=messages, tools=TOOLS)
            msg = resp.message
            print(f'iteration {i}: tool_calls={msg.tool_calls}')
            messages.append(msg.model_dump())
            if not msg.tool_calls:
                print('FINAL:', msg.content)
                break
            for tc in msg.tool_calls:
                result = await _execute_tool(tc.function.name, dict(tc.function.arguments), session)
                print('  tool result:', tc.function.name, tc.function.arguments, '->', result)
                messages.append({'role': 'tool', 'content': str(result)})
    await engine.dispose()

asyncio.run(main())
"
```
Confirmed working: the model calls `lookup_customer_history` with a real
`customer_id`, gets back a real result queried from Postgres, and folds it
into its final explanation.

### Integration Points

- `agents/rules_agent.score_transaction()` and `agents/ml_agent.score_transaction()`
  (Milestone 5) feed directly into `should_invoke()` and the prompt built for
  the LLM — this agent never recomputes scores itself.
- All DB and external calls (`lookup_customer_history`'s Postgres query,
  `check_ip_reputation`'s `ip-api.com` call, and the Ollama chat call itself)
  are wrapped in try/except, returning an `{"error": ...}` payload or a
  fallback explanation string rather than crashing the pipeline.
- Milestone 7's `main.py`/`pipeline/aggregator.py` will call `investigate()`
  after Layers 2-3 run, store the result in `AuditLog.llm_explanation`, set
  `AuditLog.llm_called`, and add `settings.LLM_WEIGHT` to `final_score` only
  when the LLM was actually invoked.

---

## Milestone 7: Pipeline Aggregation & API Wiring

> **In short:** wires every layer built so far into one live HTTP endpoint —
> submit a transaction, get back a routing decision, backed by a real audit trail.
> **Analogy:** the assembly line that finally connects every station (the two
> inspectors, the detective) into one conveyor belt a real package can travel down.

### Purpose

`pipeline/aggregator.py` (Layer 5) and `main.py` (Layer 1) close the loop: a real
HTTP request now flows through every agent built in Milestones 5-6 and comes back
with a final routing decision.

- **`main.py`** — a FastAPI app with `POST /api/v1/transaction`. Looks up the
  customer profile (404 if unknown), determines `is_new_payee` by checking
  Postgres for any prior transaction to that payee, persists the transaction,
  runs the pipeline, and returns the result.
- **`pipeline/aggregator.py`** — runs the rules and ML agents concurrently via
  `asyncio.gather()`, invokes the LLM agent only if either score clears
  `settings.LLM_INVOKE_THRESHOLD`, computes:
  ```
  final_score = rule_score * RULE_WEIGHT + anomaly_score * ML_WEIGHT
                + (LLM_WEIGHT if llm_called else 0)
  ```
  routes on `settings.LOG_ONLY_MAX` / `settings.ANALYST_QUEUE_MAX`
  (`log_only` / `analyst_queue` / `high_alert`), always writes an `AuditLog`
  row, and creates an `Alert` row only for the latter two actions.

### Architecture Diagram

```
POST /api/v1/transaction (main.py)
        │
        ├─ lookup CustomerProfile (404 if missing)
        ├─ compute is_new_payee (DB check: any prior txn to this payee?)
        ├─ INSERT Transaction, flush() -> gets transaction.id
        ▼
pipeline.aggregator.process_transaction()
        │
        ├─ asyncio.gather(rules_agent, ml_agent)  ── always runs
        │        │
        │        ▼
        │   rule_score, anomaly_score
        │        │
        ├─ should_invoke()? ──yes──► llm_agent.investigate() ── gated
        │        │
        ▼        ▼
  final_score = rule*0.40 + anomaly*0.35 + (0.15 if llm_called)
        │
        ├─ route: <0.35 log_only | <0.70 analyst_queue | >=0.70 high_alert
        ├─ UPDATE transactions (scores + action)
        ├─ INSERT audit_log (always)
        └─ INSERT alerts (only analyst_queue / high_alert)
        │
        ▼
  commit() ──► TransactionOut JSON response
```

### Usage / Testing Commands

**Start the server** (Postgres running, Milestone 4's model trained, Ollama
running — see Milestones 1/4/6):
```bash
./venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**Health check:**
```bash
curl -s http://localhost:8000/health
```

**Submit a normal-looking transaction:**
```bash
curl -s -X POST http://localhost:8000/api/v1/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST00001", "amount": 1500, "channel": "mobile",
    "merchant_category": "grocery", "merchant_name": "Norzin Lam Grocery",
    "payee_id": "PAYEE000001", "ip_address": "119.2.10.5", "ip_country": "BT",
    "transaction_time": "2026-07-15T14:00:00Z"
  }'
```
Expect `action: "log_only"` and a low `final_score`.

**Submit a fraud-shaped transaction** (high amount, blacklisted IP, foreign
country, new payee, night hour):
```bash
curl -s -X POST http://localhost:8000/api/v1/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST00001", "amount": 45000, "channel": "web",
    "merchant_category": "crypto_exchange", "merchant_name": "DrukCoin Exchange",
    "payee_id": "PAYEE999999", "ip_address": "185.220.101.1", "ip_country": "AE",
    "transaction_time": "2026-07-15T02:30:00Z"
  }'
```
Expect `rule_score: 1.0`, all 5 rules in `triggered_rules`, `action: "high_alert"`,
and a non-null `alert_id`.

**Verify the DB state matches the response:**
```bash
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c \
  "SELECT id, action, final_score FROM transactions ORDER BY id DESC LIMIT 2;"
docker exec fraud_postgres psql -U fraud_admin -d fraud_detection -c "SELECT * FROM alerts;"
```

**Submit an unknown customer_id** — expect HTTP 404:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/v1/transaction \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"CUST99999","amount":100,"channel":"mobile","merchant_category":"grocery","merchant_name":"Test","ip_address":"1.2.3.4"}'
```

### Issues hit while building this milestone (and fixes)

1. **The plan mentioned an "async queue" for Layer 1**, but adding real queue
   infrastructure (Redis/Celery) would violate the "no cloud dependencies"
   principle and wasn't needed at this scale. **Resolution:** FastAPI's async
   endpoint handler processes the transaction inline — still fully
   non-blocking (`asyncio.gather()`, async DB/HTTP calls throughout) without
   introducing new infra.
2. **Rules/ML velocity queries need the current transaction's own row to
   exist** (they count transactions with `transaction_time <= now`,
   inclusive of itself). **Fix:** `main.py` calls `session.flush()` right
   after adding the new `Transaction` — assigns `transaction.id` and makes
   the row visible to same-session queries without a full commit.
3. **Small-model quirk, not a bug:** on the fraud-shaped test transaction,
   llama3.1 wrote what looked like an attempted follow-up tool call
   (`{"name": "lookup_merchant_history", ...}`) as plain text inside its
   final answer instead of issuing a real tool call. The explanation was
   still usable and the real tool call earlier in the same conversation
   worked correctly — this is a known small-model tool-calling limitation,
   not something to patch around in the pipeline code.

### Integration Points

- Reuses `settings.RULE_WEIGHT`, `settings.ML_WEIGHT`, `settings.LLM_WEIGHT`,
  `settings.LOG_ONLY_MAX`, `settings.ANALYST_QUEUE_MAX`, and
  `settings.API_V1_PREFIX` / `APP_HOST` / `APP_PORT` — all defined since
  Milestone 2, none new.
- Calls `agents.rules_agent`, `agents.ml_agent`, and `agents.llm_agent`
  (Milestones 5-6) with no changes to those files.
- Every request that reaches `pipeline.process_transaction()` writes to
  `AuditLog` regardless of outcome — that's the compliance trail Milestone 2
  designed the table for.
- Milestone 8's React dashboard will read `alerts` (via a future `GET`
  endpoint on this same FastAPI app, not directly from Postgres) to display
  open alerts and their `llm_explanation` text.

---

## Milestone 8: Analyst React Dashboard

> **In short:** the human-facing screen an analyst actually works from — a live
> view of open alerts, the full evidence behind each one, and a place to try a
> transaction and watch the pipeline score it in real time.
> **Analogy:** the security office's monitor wall — every camera feed (agent) has
> been running this whole time, but this is the first screen a person actually
> looks at to decide what to do about it.

### Purpose

`frontend/` (React + Vite) is the last piece: an analyst dashboard that turns the
JSON the API already returns into something a human can act on.

- **`main.py`** gained three endpoints the dashboard needs: `GET /api/v1/alerts`
  (filterable by status, joined with the transaction and audit-log context),
  `PATCH /api/v1/alerts/{id}` (mark reviewed/dismissed), and `GET /api/v1/stats`
  (counts for the header tiles). CORS middleware was added so a
  separately-served frontend can call it.
- **`agents/llm_agent.py`** gained a `settings.LLM_ENABLED` guard — if there's no
  local Ollama to call (the public demo host, see below), `investigate()`
  returns a clear "not available in this environment" message instead of
  hanging or erroring against an unreachable `localhost:11434`.
- **`frontend/src/App.jsx`** — stat tiles (transactions scored, open/reviewed
  alerts, high-alert count), a filterable alert list where each row expands to
  show the full transaction, triggered rules, and LLM explanation with
  Reviewed/Dismiss actions, and a "try a live transaction" form that hits
  `POST /api/v1/transaction` and shows the real-time scoring result.
- Visual design pulls its brand color and gold accent directly from Bank of
  Bhutan's live site (`bob.bt`'s stylesheet: `#006ea9` blue, `#ffcb05` gold);
  severity/status badges use the dataviz skill's fixed, accessibility-validated
  status palette (good/warning/critical) so state colors never get reinterpreted
  as branding.

### Architecture Diagram

```
frontend/src/App.jsx (React + Vite, localhost:5173 in dev)
        │
        ├─ GET /api/v1/stats ───────────► stat tiles
        ├─ GET /api/v1/alerts?status=  ──► alert list (open/reviewed/dismissed/all)
        │        │
        │        └─ click row ──► expand: scores, triggered_rules, llm_explanation
        │                          └─ PATCH /api/v1/alerts/{id} ──► Reviewed / Dismiss
        │
        └─ POST /api/v1/transaction ────► live pipeline run (Milestone 7)
                                            └─ result card: action, final_score,
                                               triggered_rules, llm_explanation
```

### Usage / Testing Commands

**Start the backend** (Postgres running, model trained, Ollama running — see
Milestones 1/4/6):
```bash
./venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**Start the dashboard** (separate terminal):
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173`. `frontend/.env.development` points it at
`http://localhost:8000` by default (`VITE_API_BASE_URL`).

**What to check:**
- Stat tiles match `GET /api/v1/stats`.
- The "Open" filter shows only open alerts; switching to "All" shows everything.
- Expanding a row shows transaction details, triggered rules as chips, and the
  LLM explanation (or "(not invoked)" if the LLM threshold wasn't cleared).
- "Mark Reviewed" / "Dismiss" immediately move the alert out of the Open filter.
- Submitting the test-transaction form returns a real score within a few
  seconds (log_only/analyst_queue) or up to ~30-90s if it clears
  `LLM_INVOKE_THRESHOLD` and triggers a real local LLM call.

**Verified with an actual headless-browser run** (Playwright), not just a
visual read of the code — confirmed zero console errors, real data rendering
from Postgres, and a live transaction submission correctly updating the stat
tiles and alert list.

### Issues hit while building this milestone (and fixes)

1. **Test-transaction result card showed a wrong "Analyst Queue" badge even for
   `log_only` results.** The result renderer assumed every submitted
   transaction was either `high_alert` or `analyst_queue`, but `log_only` (no
   alert at all) is a valid third outcome. **Fix:** only render the severity
   badge when `action !== "log_only"`, and humanize the action text
   (`log_only` → `log only`) instead of showing the raw enum value.
2. **Repeated test submissions for the same customer stacked up slow LLM
   calls.** Ollama processes one generation at a time; firing several
   test transactions back-to-back for the same customer queued their LLM
   investigations behind each other, making later ones look "hung" when they
   were just waiting in line. Not a bug — real usage submits one transaction
   at a time — but worth knowing: a `log_only`/`analyst_queue` decision comes
   back in well under a second: it's specifically the (gated) LLM call that
   can take 30-90s+ on local CPU inference.

### Integration Points

- Talks only to the Milestone 7 API (`/api/v1/stats`, `/api/v1/alerts`,
  `/api/v1/transaction`) — no direct Postgres access from the frontend.
- `settings.CORS_ORIGINS` (Milestone 2 style: env-overridable, defaults to the
  Vite dev server) must include whatever origin the frontend is actually
  served from — the public demo deployment overrides it with the GitHub
  Pages URL (see "Public Demo Deployment" below).
- `settings.LLM_ENABLED` is the switch the public demo deployment flips off,
  since there's no local Ollama on Render — rules and ML still score live
  either way.

---

## Public Demo Deployment (beyond the 8 milestones) — PLANNED, NOT YET BUILT

Not part of the original milestone plan, but requested directly: a public,
shareable link for demos. Since the project's core rule is "100% local, no
paid APIs," the deployed version has to keep that promise by design rather
than by exception. This section documents the decided plan so it can be
picked up later — nothing below has been executed yet.

### Decisions made

- **Frontend** → **GitHub Pages** (static build of `frontend/`, deployed
  straight from this repo — free, no extra account beyond GitHub). Swapped in
  for the originally-considered Vercel option since Pages ties directly to
  the repo and needs no separate signup.
- **Backend + DB** → **Render** (FastAPI + a hosted Postgres, free tier).
  GitHub Pages only serves static files — it cannot run the FastAPI process
  or host Postgres, so the backend still needs real server hosting.
- **Rules + ML layers run live** in the hosted deployment — real scoring on
  whatever transaction a visitor submits through the demo form.
- **LLM layer does not call a cloud/paid model.** `LLM_ENABLED=false` in
  Render's environment variables makes `investigate()` (agents/llm_agent.py)
  return a clear "not available in this environment" message for any *new*
  transaction submitted on the public demo, instead of hanging or erroring
  trying to reach a `localhost:11434` that doesn't exist on Render. The
  seeded historical alerts, however, keep their **genuine** `llm_explanation`
  text — generated locally against the real Ollama + llama3.1 before the demo
  dataset was exported — so visitors still see real LLM reasoning on the
  example fraud cases, just not live for their own submissions.

This keeps the public demo honest: nothing it shows is fabricated or routed
through a paid API, and the one capability it can't reproduce (a local LLM)
is clearly labeled rather than silently faked.

### Remaining work (in order)

1. **Batch-generate LLM explanations for the seed dataset** — a new script
   (not yet written) that runs `agents/llm_agent.investigate()` locally,
   with real Ollama, against every seeded transaction whose `rule_score` or
   `anomaly_score` already clears `settings.LLM_INVOKE_THRESHOLD`, and saves
   the result into `AuditLog.llm_explanation` / `Alert.explanation` — so the
   exported demo dataset has genuine LLM text baked in before it ever reaches
   Render.
2. **Provision Render**: a Postgres instance (free tier) + a web service
   running `main.py` (`uvicorn main:app --host 0.0.0.0 --port $PORT`).
   Environment variables needed: `DATABASE_URL`-equivalent
   (`DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME` — Render's hosted
   Postgres provides these), `LLM_ENABLED=false`, and `CORS_ORIGINS` set to
   the eventual GitHub Pages URL.
3. **Load the seeded dataset (with pre-baked LLM text) into Render's
   Postgres** — export/import from the local DB once step 1 is done, rather
   than running `scripts/generate_data.py` fresh on Render (that would lose
   the pre-generated explanations and reset ground truth).
4. **Build + deploy the frontend to GitHub Pages**:
   - A GitHub Actions workflow that runs `npm run build` in `frontend/` and
     publishes `dist/` via Pages' "deploy from Actions" flow.
   - `VITE_API_BASE_URL` set at build time (via the workflow's env, not
     `.env.development`) to the real Render backend URL.
   - Vite's `base` config path set to match the Pages URL structure if the
     site isn't served from the domain root (e.g. `/LockedIn/` for a repo
     site at `username.github.io/LockedIn/`).
5. **Point `settings.CORS_ORIGINS` on Render at the final `*.github.io` URL**
   once it's known (chicken-and-egg with step 2 — may need one redeploy after
   the Pages URL is confirmed).
