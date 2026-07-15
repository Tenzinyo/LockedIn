"""Generates synthetic Bhutanese banking data and loads it into Postgres.

Creates customer profiles plus a large batch of transactions with realistic
per-customer spending baselines, Bhutan-specific merchants/currency, and a
small fraction of transactions altered to look fraudulent (high amount,
odd hours, new payee, IP country mismatch, rapid-fire bursts). The
`is_fraud` label produced here is ground truth for later milestones
(Milestone 4 model training, Milestone 5 rule/ML evaluation) — it must
never be read by the scoring agents themselves at inference time.

Run from the project root (recreates tables first, then clears any
previously generated rows so re-running is safe):
    python -m scripts.generate_data
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete

from config import settings
from db.models import (
    Alert,
    AuditLog,
    CustomerProfile,
    Transaction,
    TransactionChannel,
    async_session,
    engine,
    init_models,
)

NUM_CUSTOMERS = 500
NUM_TRANSACTIONS = 12_000
FRAUD_RATE = settings.ML_CONTAMINATION  # single source of truth: 0.02
DAYS_BACK = 60
BATCH_SIZE = 1_000

rng = np.random.default_rng(settings.ML_RANDOM_STATE)
py_rng = random.Random(settings.ML_RANDOM_STATE)

BHUTANESE_GIVEN_NAMES = [
    "Tshering", "Pema", "Dorji", "Karma", "Sonam", "Wangchuk", "Choden", "Yeshi",
    "Ugyen", "Namgyal", "Kinley", "Jigme", "Dechen", "Tandin", "Sangay", "Rinzin",
    "Kesang", "Phuntsho", "Tenzin", "Lhamo", "Zangmo", "Wangmo", "Norbu", "Chime",
    "Pelden", "Dawa", "Nima", "Zangmo", "Dema", "Choki",
]

# Bhutanese given names are typically combined (no family surname convention).
MERCHANTS = {
    "telecom": ["TashiCell", "B-Mobile"],
    "grocery": [
        "Thimphu Centenary Farmers Market", "Norzin Lam Grocery", "Changzamtog Mart",
    ],
    "utility": ["Bhutan Power Corporation", "Bhutan Telecom"],
    "retail": ["Druk Trading House", "Norling Mall", "Hong Kong Market"],
    "restaurant": ["Zombala Restaurant", "Ambient Cafe", "Folk Heritage Restaurant"],
    "electronics": ["Samden Electronics", "Norbu Electronics"],
    "jewelry": ["Druk Jewellers", "Bhutan Gems & Jewellery"],
    "money_transfer": ["Bhutan Post Money Order", "BOB Remit", "Western Union Thimphu"],
    "online_gaming": ["DrukBet Gaming", "CloudPlay Bhutan"],
    "crypto_exchange": ["DrukCoin Exchange"],
    "unknown": ["Unknown Merchant"],
}

# Weighted so everyday categories (grocery/telecom/retail) dominate and the
# high-risk ones (crypto/jewelry/gaming) stay rare, mirroring real spending mix.
CATEGORY_WEIGHTS = {
    "grocery": 0.22, "telecom": 0.14, "retail": 0.16, "restaurant": 0.16,
    "utility": 0.12, "electronics": 0.08, "money_transfer": 0.06,
    "jewelry": 0.03, "online_gaming": 0.02, "crypto_exchange": 0.005, "unknown": 0.005,
}
CATEGORIES = list(CATEGORY_WEIGHTS.keys())
CATEGORY_P = list(CATEGORY_WEIGHTS.values())

# Base transaction amount range (BTN) per category, used to derive each
# customer's average and per-transaction sampling.
CATEGORY_AMOUNT_RANGE = {
    "grocery": (100, 1_500), "telecom": (100, 800), "retail": (200, 4_000),
    "restaurant": (150, 2_000), "utility": (300, 2_500), "electronics": (1_000, 25_000),
    "money_transfer": (500, 15_000), "jewelry": (5_000, 80_000),
    "online_gaming": (200, 5_000), "crypto_exchange": (2_000, 50_000), "unknown": (100, 3_000),
}

CHANNELS = list(TransactionChannel)
CHANNEL_WEIGHTS = {"mobile": 0.45, "web": 0.20, "pos": 0.22, "atm": 0.10, "branch": 0.03}
CHANNEL_P = [CHANNEL_WEIGHTS[c.value] for c in CHANNELS]

FOREIGN_COUNTRIES = ["IN", "TH", "CN", "SG", "US", "GB", "AE"]


def random_full_name() -> str:
    first, second = py_rng.sample(BHUTANESE_GIVEN_NAMES, 2)
    return f"{first} {second}"


def random_ip(country: str) -> str:
    if country == "BT":
        return f"119.2.{py_rng.randint(0, 255)}.{py_rng.randint(1, 254)}"
    return f"{py_rng.randint(1, 223)}.{py_rng.randint(0, 255)}.{py_rng.randint(0, 255)}.{py_rng.randint(1, 254)}"


def dt_at(days_back_max: float, hour_range: tuple[int, int]) -> datetime:
    now = datetime.now(timezone.utc)
    day_offset = py_rng.uniform(0, days_back_max)
    hour = py_rng.uniform(*hour_range) % 24
    base = now - timedelta(days=day_offset)
    return base.replace(
        hour=int(hour), minute=py_rng.randint(0, 59), second=py_rng.randint(0, 59), microsecond=0
    )


def build_customers() -> list[CustomerProfile]:
    customers = []
    for i in range(1, NUM_CUSTOMERS + 1):
        usual_start = py_rng.randint(5, 8)
        customers.append(
            CustomerProfile(
                customer_id=f"CUST{i:05d}",
                full_name=random_full_name(),
                home_country="BT",
                account_age_days=py_rng.randint(30, 3650),
                avg_txn_amount=round(py_rng.uniform(300, 6_000), 2),
                usual_hour_start=usual_start,
                usual_hour_end=usual_start + py_rng.randint(12, 16),
                previous_flags_count=0,
            )
        )
    return customers


def build_transactions(customers: list[CustomerProfile]) -> list[Transaction]:
    customer_ids = [c.customer_id for c in customers]
    profile_by_id = {c.customer_id: c for c in customers}
    known_payees: dict[str, list[str]] = {cid: [] for cid in customer_ids}
    payee_counter = 0

    n_fraud_target = int(NUM_TRANSACTIONS * FRAUD_RATE)
    fraud_flags = np.zeros(NUM_TRANSACTIONS, dtype=bool)
    fraud_flags[:n_fraud_target] = True
    rng.shuffle(fraud_flags)

    transactions = []
    idx = 0
    while idx < NUM_TRANSACTIONS:
        is_fraud = bool(fraud_flags[idx])
        customer_id = py_rng.choice(customer_ids)
        profile = profile_by_id[customer_id]
        category = str(rng.choice(CATEGORIES, p=CATEGORY_P))
        low, high = CATEGORY_AMOUNT_RANGE[category]

        # ~30% of fraud cases are rapid-fire bursts (velocity pattern): 3-5
        # transactions for the same customer within the velocity window.
        burst_size = 1
        if is_fraud and py_rng.random() < 0.30:
            burst_size = py_rng.randint(3, 5)

        for b in range(burst_size):
            if idx >= NUM_TRANSACTIONS:
                break

            payees = known_payees[customer_id]
            if is_fraud:
                amount = round(profile.avg_txn_amount * py_rng.uniform(5, 20), 2)
                amount = max(amount, low)
                is_new_payee = py_rng.random() < 0.7 or not payees
                ip_country = py_rng.choice(FOREIGN_COUNTRIES) if py_rng.random() < 0.6 else "BT"
                if burst_size > 1:
                    txn_time = dt_at(DAYS_BACK, (0, 24)) - timedelta(
                        minutes=b * py_rng.uniform(0, settings.VELOCITY_WINDOW_MINUTES / burst_size)
                    )
                else:
                    txn_time = dt_at(DAYS_BACK, (settings.NIGHT_HOUR_START, settings.NIGHT_HOUR_END + 1)) \
                        if py_rng.random() < 0.5 else dt_at(DAYS_BACK, (0, 24))
            else:
                amount = round(max(rng.lognormal(mean=np.log(max(profile.avg_txn_amount, 50)), sigma=0.5), low), 2)
                amount = min(amount, high * 1.5)
                is_new_payee = py_rng.random() < 0.15 or not payees
                ip_country = "BT"
                txn_time = dt_at(DAYS_BACK, (profile.usual_hour_start, profile.usual_hour_end))

            if is_new_payee:
                payee_counter += 1
                payee_id = f"PAYEE{payee_counter:06d}"
                payees.append(payee_id)
            else:
                payee_id = py_rng.choice(payees)

            merchant_name = py_rng.choice(MERCHANTS[category])
            channel = CHANNELS[rng.choice(len(CHANNELS), p=CHANNEL_P)]

            transactions.append(
                Transaction(
                    customer_id=customer_id,
                    amount=amount,
                    currency=settings.CURRENCY,
                    channel=channel,
                    merchant_category=category,
                    payee_id=payee_id,
                    is_new_payee=is_new_payee,
                    ip_address=random_ip(ip_country),
                    ip_country=ip_country,
                    transaction_time=txn_time,
                    is_fraud=is_fraud,
                )
            )
            idx += 1
            _ = merchant_name  # merchant name is illustrative only; schema stores the category

    return transactions


async def clear_generated_data() -> None:
    async with async_session() as session:
        await session.execute(delete(Alert))
        await session.execute(delete(AuditLog))
        await session.execute(delete(Transaction))
        await session.execute(delete(CustomerProfile))
        await session.commit()


async def main() -> None:
    await init_models()
    await clear_generated_data()

    customers = build_customers()
    async with async_session() as session:
        session.add_all(customers)
        await session.commit()

    transactions = build_transactions(customers)
    for start in range(0, len(transactions), BATCH_SIZE):
        batch = transactions[start : start + BATCH_SIZE]
        async with async_session() as session:
            session.add_all(batch)
            await session.commit()

    fraud_count = sum(1 for t in transactions if t.is_fraud)
    print(f"Customers created: {len(customers)}")
    print(f"Transactions created: {len(transactions)}")
    print(f"Fraud transactions: {fraud_count} ({fraud_count / len(transactions):.2%})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
