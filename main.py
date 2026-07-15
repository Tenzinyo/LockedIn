"""FastAPI app — Layer 1 (ingestion) and the wiring for Layers 2-5.

POST /api/v1/transaction: enriches an incoming transaction from Postgres
(customer profile, is_new_payee), persists it, runs it through the scoring
pipeline (pipeline/aggregator.py), and returns the routing decision.

Run from the project root (Postgres must be running, Milestone 4's model
must exist at settings.ML_MODEL_PATH, Ollama should be running for the LLM
layer to work — see Milestones 1/4/6):
    ./venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Alert, AlertStatus, AuditLog, CustomerProfile, Transaction, TransactionChannel, async_session
from pipeline.aggregator import process_transaction

app = FastAPI(title="LockedIn Fraud Detection")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TransactionIn(BaseModel):
    customer_id: str
    amount: float
    channel: TransactionChannel
    merchant_category: str
    merchant_name: str
    payee_id: str | None = None
    ip_address: str
    ip_country: str | None = None
    transaction_time: datetime | None = None
    currency: str = settings.CURRENCY


class TransactionOut(BaseModel):
    transaction_id: int
    rule_score: float
    anomaly_score: float
    final_score: float
    action: str
    triggered_rules: list[str]
    llm_called: bool
    llm_explanation: str | None
    alert_id: int | None


class AlertOut(BaseModel):
    id: int
    transaction_id: int
    customer_id: str
    severity: str
    status: str
    explanation: str | None
    created_at: datetime
    resolved_at: datetime | None
    amount: float
    currency: str
    merchant_name: str
    merchant_category: str
    channel: str
    ip_address: str
    ip_country: str | None
    transaction_time: datetime
    rule_score: float | None
    anomaly_score: float | None
    final_score: float | None
    triggered_rules: list[str]
    llm_called: bool


class AlertStatusUpdate(BaseModel):
    status: Literal["reviewed", "dismissed"]


async def _alert_out(session: AsyncSession, alert: Alert) -> AlertOut:
    stmt = (
        select(Transaction, AuditLog)
        .join(AuditLog, AuditLog.transaction_id == Transaction.id)
        .where(Transaction.id == alert.transaction_id)
    )
    txn, audit = (await session.execute(stmt)).one()
    return AlertOut(
        id=alert.id, transaction_id=alert.transaction_id, customer_id=alert.customer_id,
        severity=alert.severity, status=alert.status.value, explanation=alert.explanation,
        created_at=alert.created_at, resolved_at=alert.resolved_at,
        amount=txn.amount, currency=txn.currency, merchant_name=txn.merchant_name,
        merchant_category=txn.merchant_category, channel=txn.channel.value,
        ip_address=txn.ip_address, ip_country=txn.ip_country, transaction_time=txn.transaction_time,
        rule_score=txn.rule_score, anomaly_score=txn.anomaly_score, final_score=txn.final_score,
        triggered_rules=audit.triggered_rules, llm_called=audit.llm_called,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(f"{settings.API_V1_PREFIX}/transaction", response_model=TransactionOut)
async def submit_transaction(payload: TransactionIn) -> TransactionOut:
    async with async_session() as session:
        customer = await session.get(CustomerProfile, payload.customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail=f"Unknown customer_id: {payload.customer_id}")

        is_new_payee = False
        if payload.payee_id:
            stmt = (
                select(Transaction.id)
                .where(Transaction.customer_id == payload.customer_id, Transaction.payee_id == payload.payee_id)
                .limit(1)
            )
            existing = (await session.execute(stmt)).first()
            is_new_payee = existing is None

        transaction = Transaction(
            customer_id=payload.customer_id,
            amount=payload.amount,
            currency=payload.currency,
            channel=payload.channel,
            merchant_category=payload.merchant_category,
            merchant_name=payload.merchant_name,
            payee_id=payload.payee_id,
            is_new_payee=is_new_payee,
            ip_address=payload.ip_address,
            ip_country=payload.ip_country,
            transaction_time=payload.transaction_time or datetime.now(timezone.utc),
            is_fraud=False,  # ground truth is unknown for real transactions
        )
        session.add(transaction)
        await session.flush()  # assigns transaction.id, visible to same-session queries

        result = await process_transaction(transaction, customer, session)
        await session.commit()

        return TransactionOut(
            transaction_id=transaction.id,
            rule_score=result.rule_score,
            anomaly_score=result.anomaly_score,
            final_score=result.final_score,
            action=result.action,
            triggered_rules=result.triggered_rules,
            llm_called=result.llm_called,
            llm_explanation=result.llm_explanation,
            alert_id=result.alert_id,
        )


@app.get(f"{settings.API_V1_PREFIX}/alerts", response_model=list[AlertOut])
async def list_alerts(status: str | None = None, limit: int = 50) -> list[AlertOut]:
    async with async_session() as session:
        stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
        if status:
            try:
                stmt = stmt.where(Alert.status == AlertStatus(status))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        alerts = (await session.execute(stmt)).scalars().all()
        return [await _alert_out(session, alert) for alert in alerts]


@app.patch(f"{settings.API_V1_PREFIX}/alerts/{{alert_id}}", response_model=AlertOut)
async def update_alert_status(alert_id: int, payload: AlertStatusUpdate) -> AlertOut:
    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail=f"Unknown alert_id: {alert_id}")
        alert.status = AlertStatus(payload.status)
        alert.resolved_at = datetime.now(timezone.utc)
        await session.commit()
        return await _alert_out(session, alert)


@app.get(f"{settings.API_V1_PREFIX}/stats")
async def stats() -> dict:
    async with async_session() as session:
        total_transactions = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
        by_status = (await session.execute(select(Alert.status, func.count(Alert.id)).group_by(Alert.status))).all()
        by_severity = (
            await session.execute(select(Alert.severity, func.count(Alert.id)).group_by(Alert.severity))
        ).all()
        return {
            "total_transactions": total_transactions,
            "alerts_by_status": {s.value: c for s, c in by_status},
            "alerts_by_severity": {sev: c for sev, c in by_severity},
        }
