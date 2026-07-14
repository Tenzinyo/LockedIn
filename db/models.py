"""SQLAlchemy models + async engine/session for the fraud_detection database.

Four tables:
- CustomerProfile: baseline behavior per customer, used to enrich incoming
  transactions (Layer 1) and as ground truth for rule checks (Layer 2).
- Transaction: every transaction event, enriched and scored as it moves
  through the pipeline.
- AuditLog: one row per transaction per pipeline run — the compliance trail.
- Alert: created for any transaction routed to analyst_queue or high_alert.

Run this file directly to create all tables in the local Postgres instance:
    python db/models.py
"""
import asyncio
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import settings


class Base(DeclarativeBase):
    pass


class TransactionChannel(str, enum.Enum):
    MOBILE = "mobile"
    WEB = "web"
    ATM = "atm"
    POS = "pos"
    BRANCH = "branch"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    customer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    home_country: Mapped[str] = mapped_column(String(2), default="BT", nullable=False)

    account_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_txn_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    usual_hour_start: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    usual_hour_end: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    previous_flags_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(back_populates="customer")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customer_profiles.customer_id"), nullable=False, index=True
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default=settings.CURRENCY, nullable=False)
    channel: Mapped[TransactionChannel] = mapped_column(
        SAEnum(TransactionChannel, native_enum=False, length=16), nullable=False
    )
    merchant_category: Mapped[str] = mapped_column(String(32), nullable=False)

    payee_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_new_payee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    ip_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    transaction_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Ground-truth label for synthetic data generation / model evaluation only —
    # never fed to the rules/ML/LLM agents at inference time.
    is_fraud: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Populated as the transaction moves through the scoring pipeline (Layers 2-5).
    rule_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer: Mapped["CustomerProfile"] = relationship(back_populates="transactions")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id"), nullable=False, index=True
    )

    rule_score: Mapped[float] = mapped_column(Float, nullable=False)
    triggered_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)

    llm_called: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="audit_logs")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customer_profiles.customer_id"), nullable=False, index=True
    )

    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # analyst_queue | high_alert
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus, native_enum=False, length=16),
        default=AlertStatus.OPEN,
        nullable=False,
        index=True,
    )
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transaction: Mapped["Transaction"] = relationship(back_populates="alerts")
    customer: Mapped["CustomerProfile"] = relationship(back_populates="alerts")


engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_models() -> None:
    """Create all tables if they don't already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init_models())
    print("Tables created: customer_profiles, transactions, audit_log, alerts")
