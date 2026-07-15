import { useEffect, useState } from "react";
import { api } from "./api";
import "./App.css";

const CHANNELS = ["mobile", "web", "atm", "pos", "branch"];
const STATUS_FILTERS = ["open", "reviewed", "dismissed", "all"];

function StatTile({ label, value, accent }) {
  return (
    <div className="stat-tile" style={accent ? { borderTopColor: accent } : undefined}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function SeverityBadge({ severity }) {
  const isHigh = severity === "high_alert";
  return (
    <span className={`badge ${isHigh ? "badge-critical" : "badge-warning"}`}>
      {isHigh ? "High Alert" : "Analyst Queue"}
    </span>
  );
}

function StatusBadge({ status }) {
  const cls = status === "open" ? "badge-warning" : status === "reviewed" ? "badge-good" : "badge-muted";
  return <span className={`badge ${cls}`}>{status}</span>;
}

function AlertRow({ alert, onUpdated }) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);

  async function setStatus(status) {
    setBusy(true);
    try {
      await api.updateAlertStatus(alert.id, status);
      onUpdated();
    } catch (err) {
      alert.error = err.message;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="alert-card">
      <button className="alert-row" onClick={() => setExpanded((v) => !v)}>
        <SeverityBadge severity={alert.severity} />
        <span className="alert-cell alert-customer">{alert.customer_id}</span>
        <span className="alert-cell alert-amount">
          {alert.amount.toLocaleString()} {alert.currency}
        </span>
        <span className="alert-cell alert-merchant">{alert.merchant_name}</span>
        <span className="alert-cell alert-score">{alert.final_score?.toFixed(2)}</span>
        <StatusBadge status={alert.status} />
        <span className="chevron">{expanded ? "−" : "+"}</span>
      </button>

      {expanded && (
        <div className="alert-detail">
          <div className="detail-grid">
            <div>
              <div className="detail-label">Transaction time</div>
              <div>{new Date(alert.transaction_time).toLocaleString()}</div>
            </div>
            <div>
              <div className="detail-label">Channel</div>
              <div>{alert.channel}</div>
            </div>
            <div>
              <div className="detail-label">Merchant category</div>
              <div>{alert.merchant_category}</div>
            </div>
            <div>
              <div className="detail-label">IP address</div>
              <div>
                {alert.ip_address} {alert.ip_country ? `(${alert.ip_country})` : ""}
              </div>
            </div>
            <div>
              <div className="detail-label">Rule score</div>
              <div>{alert.rule_score?.toFixed(2)}</div>
            </div>
            <div>
              <div className="detail-label">Anomaly score</div>
              <div>{alert.anomaly_score?.toFixed(2)}</div>
            </div>
          </div>

          {alert.triggered_rules?.length > 0 && (
            <div className="chip-row">
              {alert.triggered_rules.map((rule) => (
                <span key={rule} className="chip">
                  {rule.replaceAll("_", " ")}
                </span>
              ))}
            </div>
          )}

          <div className="detail-label">LLM explanation {alert.llm_called ? "" : "(not invoked)"}</div>
          <p className="explanation">{alert.explanation || "—"}</p>

          {alert.status === "open" && (
            <div className="action-row">
              <button className="btn btn-primary" disabled={busy} onClick={() => setStatus("reviewed")}>
                Mark Reviewed
              </button>
              <button className="btn btn-outline" disabled={busy} onClick={() => setStatus("dismissed")}>
                Dismiss
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TransactionForm({ onScored }) {
  const [form, setForm] = useState({
    customer_id: "", amount: "", channel: "mobile", merchant_category: "grocery",
    merchant_name: "", payee_id: "", ip_address: "", ip_country: "",
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const payload = {
        ...form,
        amount: parseFloat(form.amount),
        payee_id: form.payee_id || null,
        ip_country: form.ip_country || null,
      };
      const res = await api.submitTransaction(payload);
      setResult(res);
      onScored();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="panel">
      <h2>Try a live transaction</h2>
      <p className="panel-subtitle">
        Runs the real rules + ML agents against this transaction right now.
      </p>
      <form className="txn-form" onSubmit={submit}>
        <input required placeholder="customer_id (e.g. CUST00001)" value={form.customer_id}
          onChange={(e) => update("customer_id", e.target.value)} />
        <input required type="number" step="0.01" placeholder="amount" value={form.amount}
          onChange={(e) => update("amount", e.target.value)} />
        <select value={form.channel} onChange={(e) => update("channel", e.target.value)}>
          {CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <input required placeholder="merchant_category" value={form.merchant_category}
          onChange={(e) => update("merchant_category", e.target.value)} />
        <input required placeholder="merchant_name" value={form.merchant_name}
          onChange={(e) => update("merchant_name", e.target.value)} />
        <input placeholder="payee_id (optional)" value={form.payee_id}
          onChange={(e) => update("payee_id", e.target.value)} />
        <input required placeholder="ip_address" value={form.ip_address}
          onChange={(e) => update("ip_address", e.target.value)} />
        <input placeholder="ip_country (optional, e.g. BT)" value={form.ip_country}
          onChange={(e) => update("ip_country", e.target.value)} />
        <button className="btn btn-gold" type="submit" disabled={submitting}>
          {submitting ? "Scoring…" : "Submit Transaction"}
        </button>
      </form>

      {error && <p className="form-error">{error}</p>}

      {result && (
        <div className="result-card">
          <div className="result-header">
            {result.action !== "log_only" && <SeverityBadge severity={result.action} />}
            <span className="badge badge-muted">{result.action.replaceAll("_", " ")}</span>
            <span className="result-score">final_score {result.final_score.toFixed(2)}</span>
          </div>
          {result.triggered_rules.length > 0 && (
            <div className="chip-row">
              {result.triggered_rules.map((rule) => (
                <span key={rule} className="chip">{rule.replaceAll("_", " ")}</span>
              ))}
            </div>
          )}
          {result.llm_explanation && <p className="explanation">{result.llm_explanation}</p>}
        </div>
      )}
    </section>
  );
}

function App() {
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [statusFilter, setStatusFilter] = useState("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [statsRes, alertsRes] = await Promise.all([
        api.getStats(),
        api.getAlerts(statusFilter === "all" ? null : statusFilter),
      ]);
      setStats(statsRes);
      setAlerts(alertsRes);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-name">LockedIn</span>
            <span className="brand-sub">Bank of Bhutan · Fraud Detection</span>
          </div>
        </div>
        <div className="topbar-accent" />
      </header>

      <main className="page">
        <div className="stats-row">
          <StatTile label="Transactions scored" value={stats?.total_transactions ?? "—"} />
          <StatTile label="Open alerts" value={stats?.alerts_by_status?.open ?? 0} accent="var(--warning)" />
          <StatTile label="Reviewed" value={stats?.alerts_by_status?.reviewed ?? 0} accent="var(--good)" />
          <StatTile label="High alert" value={stats?.alerts_by_severity?.high_alert ?? 0} accent="var(--critical)" />
        </div>

        <section className="panel">
          <div className="panel-header">
            <h2>Alerts</h2>
            <div className="filter-bar">
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s}
                  className={`filter-btn ${statusFilter === s ? "filter-btn-active" : ""}`}
                  onClick={() => setStatusFilter(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {loading && <p className="muted">Loading…</p>}
          {error && <p className="form-error">{error}</p>}
          {!loading && !error && alerts.length === 0 && <p className="muted">No alerts in this view.</p>}

          <div className="alerts-list">
            {alerts.map((alert) => (
              <AlertRow key={alert.id} alert={alert} onUpdated={load} />
            ))}
          </div>
        </section>

        <TransactionForm onScored={load} />
      </main>
    </>
  );
}

export default App;
