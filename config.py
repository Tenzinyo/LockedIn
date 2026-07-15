"""Central configuration for the LockedIn fraud detection system.

All thresholds, weights, and connection settings live here so no other
module hardcodes a magic number. Values can be overridden via a local
.env file (loaded automatically); sensible local-dev defaults match
docker-compose.yml so the system runs out of the box with zero setup.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


class Settings:
    # --- Database (matches docker-compose.yml) ---
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = _env_int("DB_PORT", 5433)
    DB_USER: str = os.getenv("DB_USER", "fraud_admin")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "fraud_local_dev")
    DB_NAME: str = os.getenv("DB_NAME", "fraud_detection")

    @property
    def DATABASE_URL(self) -> str:
        """Async URL for SQLAlchemy + asyncpg."""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # --- API server ---
    API_V1_PREFIX: str = "/api/v1"
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = _env_int("APP_PORT", 8000)

    # --- Currency ---
    CURRENCY: str = "BTN"

    # --- Rules Agent (agents/rules_agent.py) — each rule adds to rule_score, capped at 1.0 ---
    RULE_SCORE_CAP: float = 1.0

    HIGH_AMOUNT_MULTIPLIER: float = _env_float("HIGH_AMOUNT_MULTIPLIER", 5.0)
    HIGH_AMOUNT_SCORE: float = _env_float("HIGH_AMOUNT_SCORE", 0.30)

    VELOCITY_TXN_COUNT_THRESHOLD: int = _env_int("VELOCITY_TXN_COUNT_THRESHOLD", 3)
    VELOCITY_WINDOW_MINUTES: int = _env_int("VELOCITY_WINDOW_MINUTES", 10)
    VELOCITY_SCORE: float = _env_float("VELOCITY_SCORE", 0.25)

    IP_COUNTRY_MISMATCH_SCORE: float = _env_float("IP_COUNTRY_MISMATCH_SCORE", 0.20)

    NEW_PAYEE_AMOUNT_THRESHOLD: float = _env_float("NEW_PAYEE_AMOUNT_THRESHOLD", 10_000.0)
    NEW_PAYEE_SCORE: float = _env_float("NEW_PAYEE_SCORE", 0.20)

    NIGHT_HOUR_START: int = _env_int("NIGHT_HOUR_START", 1)   # 01:00 local
    NIGHT_HOUR_END: int = _env_int("NIGHT_HOUR_END", 4)       # 04:00 local
    NIGHT_HOUR_SCORE: float = _env_float("NIGHT_HOUR_SCORE", 0.10)

    IP_BLACKLIST_SCORE: float = _env_float("IP_BLACKLIST_SCORE", 0.35)

    # Static list of known-bad IPs for local dev/testing (no external threat
    # feed — everything must run offline). Real deployments would swap this
    # for a maintained blocklist.
    IP_BLACKLIST: list = [
        "203.0.113.66",
        "198.51.100.23",
        "185.220.101.1",
        "45.155.204.10",
    ]

    # --- ML Agent (agents/ml_agent.py) ---
    ML_CONTAMINATION: float = _env_float("ML_CONTAMINATION", 0.02)
    ML_MODEL_PATH: str = os.getenv("ML_MODEL_PATH", "models/fraud_model.pkl")
    ML_TXN_COUNT_WINDOW_MINUTES: int = _env_int("ML_TXN_COUNT_WINDOW_MINUTES", 60)
    ML_RANDOM_STATE: int = _env_int("ML_RANDOM_STATE", 42)

    # Fixed ordinal encoding for the `channel` feature — shared by
    # scripts/train_model.py and agents/ml_agent.py so training and inference
    # never drift apart on what each channel value means.
    CHANNEL_ENCODING: dict = {
        "mobile": 0,
        "web": 1,
        "atm": 2,
        "pos": 3,
        "branch": 4,
    }

    # --- LLM Agent (agents/llm_agent.py) ---
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1")
    LLM_INVOKE_THRESHOLD: float = _env_float("LLM_INVOKE_THRESHOLD", 0.20)
    LLM_MAX_TOOL_ITERATIONS: int = _env_int("LLM_MAX_TOOL_ITERATIONS", 5)

    IP_API_URL: str = os.getenv("IP_API_URL", "http://ip-api.com/json")
    IP_API_TIMEOUT_SECONDS: float = _env_float("IP_API_TIMEOUT_SECONDS", 5.0)

    CUSTOMER_HISTORY_LOOKBACK: int = _env_int("CUSTOMER_HISTORY_LOOKBACK", 30)

    # Static merchant category risk lookup used by get_merchant_risk(); 0.0 = low risk,
    # 1.0 = high risk. Bhutan-specific categories to match the synthetic dataset.
    MERCHANT_RISK_SCORES: dict = {
        "telecom": 0.10,          # e.g. TashiCell, B-Mobile
        "grocery": 0.05,          # e.g. Thimphu Centenary Farmers Market
        "utility": 0.05,
        "retail": 0.15,
        "restaurant": 0.10,
        "electronics": 0.35,
        "jewelry": 0.55,
        "money_transfer": 0.60,
        "online_gaming": 0.65,
        "crypto_exchange": 0.85,
        "unknown": 0.50,
    }

    # --- Aggregator (pipeline/aggregator.py) ---
    RULE_WEIGHT: float = _env_float("RULE_WEIGHT", 0.40)
    ML_WEIGHT: float = _env_float("ML_WEIGHT", 0.35)
    LLM_WEIGHT: float = _env_float("LLM_WEIGHT", 0.15)

    LOG_ONLY_MAX: float = _env_float("LOG_ONLY_MAX", 0.35)          # score < this -> log_only
    ANALYST_QUEUE_MAX: float = _env_float("ANALYST_QUEUE_MAX", 0.70)  # score < this -> analyst_queue
    # score >= ANALYST_QUEUE_MAX -> high_alert


settings = Settings()
