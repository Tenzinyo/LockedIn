"""Trains the offline anomaly-detection model used by Milestone 5's ML agent.

Pulls every transaction generated in Milestone 3 out of Postgres, engineers
the same six features `agents/ml_agent.py` will compute at inference time,
fits a scikit-learn IsolationForest, and saves it to `settings.ML_MODEL_PATH`
via joblib. `is_fraud` is read only to evaluate the trained model against
ground truth afterwards — it is never one of the model's input features,
since real transactions won't have that label at inference time.

Run from the project root (Postgres must be running and Milestone 3's data
must already be generated):
    python -m scripts.train_model
"""
import asyncio
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from sqlalchemy import select

from agents.features import FEATURE_COLUMNS, amount_to_avg_ratio
from config import settings
from db.models import CustomerProfile, Transaction, async_session, engine


async def load_transactions_df() -> pd.DataFrame:
    async with async_session() as session:
        result = await session.execute(
            select(
                Transaction.customer_id,
                Transaction.amount,
                Transaction.channel,
                Transaction.is_new_payee,
                Transaction.transaction_time,
                Transaction.is_fraud,
                CustomerProfile.avg_txn_amount,
            ).join(CustomerProfile, Transaction.customer_id == CustomerProfile.customer_id)
        )
        rows = result.all()

    df = pd.DataFrame(
        rows,
        columns=[
            "customer_id", "amount", "channel", "is_new_payee",
            "transaction_time", "is_fraud", "avg_txn_amount",
        ],
    )
    df["channel"] = df["channel"].apply(lambda c: c.value if hasattr(c, "value") else c)
    return df


def compute_velocity_counts(times: np.ndarray, window_minutes: int) -> np.ndarray:
    """Two-pointer sliding window: for each timestamp (ascending order), counts
    how many timestamps in the same array fall within `window_minutes` before it
    (inclusive of itself). `times` must already be sorted ascending."""
    window = np.timedelta64(window_minutes, "m")
    counts = np.empty(len(times), dtype=int)
    start = 0
    for i in range(len(times)):
        while times[i] - times[start] > window:
            start += 1
        counts[i] = i - start + 1
    return counts


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["customer_id", "transaction_time"]).reset_index(drop=True)

    df["amount_to_avg_ratio"] = df.apply(
        lambda row: amount_to_avg_ratio(row["amount"], row["avg_txn_amount"]), axis=1
    )

    df["hour"] = df["transaction_time"].dt.hour
    df["day_of_week"] = df["transaction_time"].dt.dayofweek

    df["txn_count_60min"] = df.groupby("customer_id")["transaction_time"].transform(
        lambda times: compute_velocity_counts(
            times.to_numpy(), settings.ML_TXN_COUNT_WINDOW_MINUTES
        )
    )

    df["is_new_payee"] = df["is_new_payee"].astype(int)
    df["channel_encoded"] = df["channel"].map(settings.CHANNEL_ENCODING)

    return df


async def main() -> None:
    df = await load_transactions_df()
    if df.empty:
        raise RuntimeError("No transactions found — run `python -m scripts.generate_data` first.")

    df = engineer_features(df)
    X = df[FEATURE_COLUMNS].to_numpy()

    model = IsolationForest(
        contamination=settings.ML_CONTAMINATION,
        random_state=settings.ML_RANDOM_STATE,
        n_estimators=200,
    )
    model.fit(X)

    # IsolationForest.predict: -1 = anomaly, 1 = normal. Map to the same
    # fraud/not-fraud convention as the ground-truth label for evaluation.
    predicted_fraud = model.predict(X) == -1

    print(f"Trained on {len(df)} transactions, {FEATURE_COLUMNS}")
    print(f"Flagged as anomalies: {predicted_fraud.sum()} ({predicted_fraud.mean():.2%})")
    print("\nConfusion matrix (rows=actual, cols=predicted; order=[not_fraud, fraud]):")
    print(confusion_matrix(df["is_fraud"], predicted_fraud))
    print("\nClassification report:")
    print(classification_report(df["is_fraud"], predicted_fraud, target_names=["not_fraud", "fraud"]))

    # decision_function: high = normal, low/negative = anomalous, but it's
    # unbounded — agents/ml_agent.py needs a 0-1 score, so we save the 1st/99th
    # percentile of training scores here and linearly rescale against them at
    # inference time (clipped to [0,1] for scores outside that range).
    decision_scores = model.decision_function(X)
    score_low, score_high = np.percentile(decision_scores, [1, 99])

    os.makedirs(os.path.dirname(settings.ML_MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "score_low": score_low, "score_high": score_high}, settings.ML_MODEL_PATH)
    print(f"Model saved to {settings.ML_MODEL_PATH} (score_low={score_low:.4f}, score_high={score_high:.4f})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
