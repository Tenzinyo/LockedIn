"""IsolationForest anomaly scoring — Layer 3 of the pipeline.

Loads the model trained in Milestone 4 (scripts/train_model.py) once at
import time and scores a single transaction using the same six features
(agents/features.py) computed at training time — same order, same encoding,
so the model never sees inputs shaped differently than what it learned on.
`decision_function`'s raw, unbounded score is linearly rescaled against the
1st/99th percentile bounds saved alongside the model, so anomaly_score
always lands in [0, 1] (higher = more anomalous).

Test standalone against Milestone 3's synthetic data:
    python -m agents.ml_agent
"""
import asyncio
from datetime import timedelta

import joblib
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.features import amount_to_avg_ratio, encode_channel
from config import settings
from db.models import CustomerProfile, Transaction, async_session, engine

try:
    _artifact = joblib.load(settings.ML_MODEL_PATH)
    _model = _artifact["model"]
    _score_low = _artifact["score_low"]
    _score_high = _artifact["score_high"]
except FileNotFoundError:
    _model = None
    _score_low = _score_high = 0.0


async def _txn_count_window(session: AsyncSession, transaction: Transaction) -> int:
    """Counts this customer's transactions in the trailing ML velocity
    window, inclusive of the transaction being scored — same convention
    used to build txn_count_60min at training time."""
    window_start = transaction.transaction_time - timedelta(minutes=settings.ML_TXN_COUNT_WINDOW_MINUTES)
    stmt = select(func.count()).select_from(Transaction).where(
        Transaction.customer_id == transaction.customer_id,
        Transaction.transaction_time <= transaction.transaction_time,
        Transaction.transaction_time > window_start,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def score_transaction(
    transaction: Transaction, customer: CustomerProfile, session: AsyncSession
) -> float:
    if _model is None:
        raise RuntimeError(
            f"No trained model at {settings.ML_MODEL_PATH} — run `python -m scripts.train_model` first."
        )

    try:
        txn_count = await _txn_count_window(session, transaction)
    except Exception:
        txn_count = 1

    features = [[
        amount_to_avg_ratio(transaction.amount, customer.avg_txn_amount),
        transaction.transaction_time.hour,
        transaction.transaction_time.weekday(),
        txn_count,
        int(transaction.is_new_payee),
        encode_channel(transaction.channel.value),
    ]]

    raw = _model.decision_function(features)[0]
    if _score_high == _score_low:
        return 0.5
    anomaly_score = (_score_high - raw) / (_score_high - _score_low)
    return float(min(max(anomaly_score, 0.0), 1.0))


async def _smoke_test() -> None:
    async with async_session() as session:
        for label, is_fraud in (("FRAUD", True), ("NORMAL", False)):
            stmt = (
                select(Transaction, CustomerProfile)
                .join(CustomerProfile, Transaction.customer_id == CustomerProfile.customer_id)
                .where(Transaction.is_fraud == is_fraud)
                .limit(5)
            )
            rows = (await session.execute(stmt)).all()
            print(f"\n--- {label} sample ---")
            for transaction, customer in rows:
                anomaly_score = await score_transaction(transaction, customer, session)
                assert 0.0 <= anomaly_score <= 1.0
                print(f"txn={transaction.id} anomaly_score={anomaly_score:.3f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_smoke_test())
