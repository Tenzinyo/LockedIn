"""Feature engineering shared between offline training (scripts/train_model.py)
and online inference (agents/ml_agent.py) so the two never drift apart on
what a feature means. Order matters: it must match FEATURE_COLUMNS exactly,
since the trained model has no column names — just positions.
"""
from config import settings

FEATURE_COLUMNS = [
    "amount_to_avg_ratio",
    "hour",
    "day_of_week",
    "txn_count_60min",
    "is_new_payee",
    "channel_encoded",
]


def amount_to_avg_ratio(amount: float, avg_txn_amount: float) -> float:
    """Falls back to the raw amount when a customer has no baseline yet
    (avg_txn_amount == 0), matching how new-customer rows are handled in
    both the training data and at inference time."""
    if not avg_txn_amount:
        return amount
    return amount / avg_txn_amount


def encode_channel(channel: str) -> int:
    return settings.CHANNEL_ENCODING[channel]
