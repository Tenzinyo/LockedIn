"""Aggregates Layer 2 (rules) + Layer 3 (ML) + Layer 4 (LLM) into a final
score and routing decision — Layer 5 of the pipeline.

Rules and ML always run together via asyncio.gather() (cheap, always worth
computing); the LLM only runs if either score clears
settings.LLM_INVOKE_THRESHOLD (expensive, gated). Every transaction gets an
AuditLog row regardless of the outcome — that's the compliance trail.

Routing (settings.LOG_ONLY_MAX / settings.ANALYST_QUEUE_MAX):
    final_score <  LOG_ONLY_MAX      -> "log_only"
    final_score <  ANALYST_QUEUE_MAX -> "analyst_queue"
    final_score >= ANALYST_QUEUE_MAX -> "high_alert"
"""
import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.llm_agent import investigate, should_invoke
from agents.ml_agent import score_transaction as ml_score_transaction
from agents.rules_agent import score_transaction as rules_score_transaction
from config import settings
from db.models import Alert, AuditLog, CustomerProfile, Transaction


@dataclass
class PipelineResult:
    rule_score: float
    anomaly_score: float
    triggered_rules: list[str]
    llm_called: bool
    llm_explanation: str | None
    final_score: float
    action: str
    alert_id: int | None


def _route(final_score: float) -> str:
    if final_score < settings.LOG_ONLY_MAX:
        return "log_only"
    if final_score < settings.ANALYST_QUEUE_MAX:
        return "analyst_queue"
    return "high_alert"


async def process_transaction(
    transaction: Transaction, customer: CustomerProfile, session: AsyncSession
) -> PipelineResult:
    """`transaction` must already be flushed (have an id) before calling this,
    since rules/ML velocity queries and AuditLog/Alert foreign keys need it."""
    rule_result, anomaly_score = await asyncio.gather(
        rules_score_transaction(transaction, customer, session),
        ml_score_transaction(transaction, customer, session),
    )

    llm_called = should_invoke(rule_result.rule_score, anomaly_score)
    llm_explanation = None
    if llm_called:
        llm_explanation = await investigate(
            transaction, customer, rule_result.rule_score, anomaly_score,
            rule_result.triggered_rules, session,
        )

    final_score = min(
        rule_result.rule_score * settings.RULE_WEIGHT
        + anomaly_score * settings.ML_WEIGHT
        + (settings.LLM_WEIGHT if llm_called else 0.0),
        1.0,
    )
    action = _route(final_score)

    transaction.rule_score = rule_result.rule_score
    transaction.anomaly_score = anomaly_score
    transaction.final_score = final_score
    transaction.action = action

    session.add(
        AuditLog(
            transaction_id=transaction.id,
            rule_score=rule_result.rule_score,
            triggered_rules=rule_result.triggered_rules,
            anomaly_score=anomaly_score,
            llm_called=llm_called,
            llm_explanation=llm_explanation,
            final_score=final_score,
            action=action,
        )
    )

    alert_id = None
    if action in ("analyst_queue", "high_alert"):
        alert = Alert(
            transaction_id=transaction.id,
            customer_id=customer.customer_id,
            severity=action,
            explanation=llm_explanation,
        )
        session.add(alert)
        await session.flush()
        alert_id = alert.id

    return PipelineResult(
        rule_score=rule_result.rule_score,
        anomaly_score=anomaly_score,
        triggered_rules=rule_result.triggered_rules,
        llm_called=llm_called,
        llm_explanation=llm_explanation,
        final_score=final_score,
        action=action,
        alert_id=alert_id,
    )
