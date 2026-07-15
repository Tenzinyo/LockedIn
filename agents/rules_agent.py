"""Deterministic rule-based fraud scoring — Layer 2 of the pipeline.

Six independent checks each add a fixed weight to `rule_score` (capped at
`settings.RULE_SCORE_CAP`). Every threshold and weight lives in config.py —
this file only implements the checks, never hardcodes a number.

Test standalone against Milestone 3's synthetic data:
    python -m agents.rules_agent
"""
import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import CustomerProfile, Transaction, async_session, engine


@dataclass
class RuleResult:
    rule_score: float
    triggered_rules: list[str] = field(default_factory=list)


async def _velocity_count(session: AsyncSession, transaction: Transaction) -> int:
    """Counts this customer's transactions in the trailing velocity window,
    inclusive of the transaction being scored."""
    window_start = transaction.transaction_time - timedelta(minutes=settings.VELOCITY_WINDOW_MINUTES)
    stmt = select(func.count()).select_from(Transaction).where(
        Transaction.customer_id == transaction.customer_id,
        Transaction.transaction_time <= transaction.transaction_time,
        Transaction.transaction_time > window_start,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def score_transaction(
    transaction: Transaction, customer: CustomerProfile, session: AsyncSession
) -> RuleResult:
    score = 0.0
    triggered: list[str] = []

    if (
        customer.avg_txn_amount > 0
        and transaction.amount > settings.HIGH_AMOUNT_MULTIPLIER * customer.avg_txn_amount
    ):
        score += settings.HIGH_AMOUNT_SCORE
        triggered.append("high_amount")

    try:
        velocity_count = await _velocity_count(session, transaction)
    except Exception:
        velocity_count = 0
    if velocity_count > settings.VELOCITY_TXN_COUNT_THRESHOLD:
        score += settings.VELOCITY_SCORE
        triggered.append("velocity")

    if transaction.ip_country and transaction.ip_country != customer.home_country:
        score += settings.IP_COUNTRY_MISMATCH_SCORE
        triggered.append("ip_country_mismatch")

    if transaction.is_new_payee and transaction.amount > settings.NEW_PAYEE_AMOUNT_THRESHOLD:
        score += settings.NEW_PAYEE_SCORE
        triggered.append("new_payee_high_amount")

    hour = transaction.transaction_time.hour
    if settings.NIGHT_HOUR_START <= hour <= settings.NIGHT_HOUR_END:
        score += settings.NIGHT_HOUR_SCORE
        triggered.append("night_hour")

    if transaction.ip_address in settings.IP_BLACKLIST:
        score += settings.IP_BLACKLIST_SCORE
        triggered.append("ip_blacklist")

    return RuleResult(rule_score=min(score, settings.RULE_SCORE_CAP), triggered_rules=triggered)


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
                result = await score_transaction(transaction, customer, session)
                assert 0.0 <= result.rule_score <= 1.0
                print(f"txn={transaction.id} rule_score={result.rule_score:.2f} triggered={result.triggered_rules}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_smoke_test())
