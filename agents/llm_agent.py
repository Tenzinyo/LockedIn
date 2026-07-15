"""Local LLM reasoning agent — Layer 4 of the pipeline.

Only invoked when a transaction's rule_score or anomaly_score exceeds
settings.LLM_INVOKE_THRESHOLD (cheap layers 2-3 triage first; the LLM is the
expensive layer). Gives Ollama's llama3.1 three tools to investigate a
flagged transaction — lookup_customer_history, check_ip_reputation,
get_merchant_risk — before producing a short natural-language explanation
for the analyst. That explanation (not a score) is the LLM's output: per
the aggregator design (Milestone 7), invoking the LLM contributes a flat
settings.LLM_WEIGHT bump to final_score, since its value here is the audit
trail it leaves for a human reviewer, not a further numeric multiplier.

Test standalone against Milestone 3's synthetic data (Ollama must be
running locally with llama3.1 pulled — see Milestone 1):
    python -m agents.llm_agent
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import ollama
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.ml_agent import score_transaction as ml_score_transaction
from agents.rules_agent import score_transaction as rules_score_transaction
from config import settings
from db.models import CustomerProfile, Transaction, async_session, engine

SYSTEM_PROMPT = (
    "You are a fraud analyst assistant for a bank in Bhutan. You are given a "
    "transaction that a rules engine and an anomaly-detection model have "
    "already flagged for review. Use the available tools to investigate "
    "before concluding. Then give a concise 2-3 sentence explanation of "
    "whether this looks fraudulent and why, referencing what you found. "
    "Do not call more tools once you have enough information to conclude."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_customer_history",
            "description": (
                "Looks up a customer's recent transaction history and prior "
                "flag count to establish their normal behavior baseline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The customer's ID, e.g. CUST00042"},
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_ip_reputation",
            "description": (
                "Looks up an IP address's geolocation and network reputation "
                "(country, ISP, whether it's a known proxy/hosting provider) "
                "via a free public IP lookup service."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ip_address": {"type": "string", "description": "The IP address to look up"},
                },
                "required": ["ip_address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_merchant_risk",
            "description": "Returns the baseline risk score (0-1) for a merchant category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant_category": {
                        "type": "string",
                        "description": "e.g. grocery, jewelry, crypto_exchange",
                    },
                },
                "required": ["merchant_category"],
            },
        },
    },
]


def should_invoke(rule_score: float, anomaly_score: float) -> bool:
    return max(rule_score, anomaly_score) > settings.LLM_INVOKE_THRESHOLD


async def lookup_customer_history(session: AsyncSession, customer_id: str) -> dict:
    """Recent-history summary only — never exposes the is_fraud ground-truth
    label, since real transactions won't have it at inference time."""
    try:
        window_start = datetime.now(timezone.utc) - timedelta(days=settings.CUSTOMER_HISTORY_LOOKBACK)
        stmt = select(
            func.count(Transaction.id),
            func.avg(Transaction.amount),
            func.count(func.distinct(Transaction.payee_id)),
        ).where(Transaction.customer_id == customer_id, Transaction.transaction_time >= window_start)
        count, avg_amount, distinct_payees = (await session.execute(stmt)).one()

        profile_stmt = select(CustomerProfile).where(CustomerProfile.customer_id == customer_id)
        profile = (await session.execute(profile_stmt)).scalar_one_or_none()

        return {
            "transactions_last_30_days": count or 0,
            "avg_amount_last_30_days": round(avg_amount, 2) if avg_amount else 0.0,
            "distinct_payees_last_30_days": distinct_payees or 0,
            "account_age_days": profile.account_age_days if profile else None,
            "previous_flags_count": profile.previous_flags_count if profile else None,
        }
    except Exception as e:
        return {"error": str(e)}


async def check_ip_reputation(ip_address: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=settings.IP_API_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{settings.IP_API_URL}/{ip_address}")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}


def get_merchant_risk(merchant_category: str) -> dict:
    risk_score = settings.MERCHANT_RISK_SCORES.get(merchant_category, settings.MERCHANT_RISK_SCORES["unknown"])
    return {"merchant_category": merchant_category, "risk_score": risk_score}


async def _execute_tool(name: str, arguments: dict, session: AsyncSession) -> dict:
    if name == "lookup_customer_history":
        return await lookup_customer_history(session, arguments.get("customer_id", ""))
    if name == "check_ip_reputation":
        return await check_ip_reputation(arguments.get("ip_address", ""))
    if name == "get_merchant_risk":
        return get_merchant_risk(arguments.get("merchant_category", ""))
    return {"error": f"unknown tool: {name}"}


def _build_user_prompt(
    transaction: Transaction, customer: CustomerProfile, rule_score: float,
    anomaly_score: float, triggered_rules: list[str],
) -> str:
    return (
        f"Transaction:\n"
        f"- customer_id: {transaction.customer_id}\n"
        f"- amount: {transaction.amount} {transaction.currency}\n"
        f"- merchant: {transaction.merchant_name} ({transaction.merchant_category})\n"
        f"- channel: {transaction.channel.value}\n"
        f"- is_new_payee: {transaction.is_new_payee}\n"
        f"- ip_address: {transaction.ip_address} (reported country: {transaction.ip_country})\n"
        f"- time: {transaction.transaction_time.isoformat()}\n\n"
        f"Customer baseline: home_country={customer.home_country}, "
        f"avg_txn_amount={customer.avg_txn_amount}\n\n"
        f"Rules engine: rule_score={rule_score:.2f}, triggered={triggered_rules}\n"
        f"ML anomaly score: {anomaly_score:.2f}"
    )


async def investigate(
    transaction: Transaction, customer: CustomerProfile, rule_score: float,
    anomaly_score: float, triggered_rules: list[str], session: AsyncSession,
) -> str | None:
    if not should_invoke(rule_score, anomaly_score):
        return None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(transaction, customer, rule_score, anomaly_score, triggered_rules)},
    ]

    try:
        client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        for _ in range(settings.LLM_MAX_TOOL_ITERATIONS):
            response = await client.chat(model=settings.OLLAMA_MODEL, messages=messages, tools=TOOLS)
            message = response.message
            messages.append(message.model_dump())

            if not message.tool_calls:
                return message.content

            for tool_call in message.tool_calls:
                result = await _execute_tool(tool_call.function.name, dict(tool_call.function.arguments), session)
                messages.append({"role": "tool", "content": json.dumps(result)})

        return "LLM investigation did not converge within max tool iterations."
    except Exception as e:
        return f"LLM investigation failed: {e}"


async def _smoke_test() -> None:
    async with async_session() as session:
        stmt = (
            select(Transaction, CustomerProfile)
            .join(CustomerProfile, Transaction.customer_id == CustomerProfile.customer_id)
            .where(Transaction.is_fraud.is_(True))
            .limit(2)
        )
        rows = (await session.execute(stmt)).all()
        for transaction, customer in rows:
            rule_result = await rules_score_transaction(transaction, customer, session)
            anomaly_score = await ml_score_transaction(transaction, customer, session)
            explanation = await investigate(
                transaction, customer, rule_result.rule_score, anomaly_score,
                rule_result.triggered_rules, session,
            )
            print(f"\ntxn={transaction.id} rule_score={rule_result.rule_score:.2f} anomaly_score={anomaly_score:.2f}")
            print(f"llm_explanation: {explanation}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_smoke_test())
