"""SQLite persistence (SQLAlchemy 2.0). Everything the dashboard or a later
analysis needs is here: orders, fills, paper positions and cash, equity
snapshots and the trade journal."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .models import (
    Account,
    EquityPoint,
    Fill,
    JournalEntry,
    Market,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Thesis,
    ThesisStatus,
    TimeInForce,
    utcnow,
)


class Base(DeclarativeBase):
    pass


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    venue_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    order_type: Mapped[str] = mapped_column(String(16))
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tif: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), index=True)
    avg_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_model(self) -> Order:
        return Order(
            id=self.id, venue=self.venue, venue_order_id=self.venue_order_id, symbol=self.symbol,
            market=Market(self.market), currency=self.currency, side=Side(self.side), qty=self.qty,
            filled_qty=self.filled_qty, order_type=OrderType(self.order_type), limit_price=self.limit_price,
            stop_price=self.stop_price, tif=TimeInForce(self.tif), status=OrderStatus(self.status),
            avg_fill_price=self.avg_fill_price, fees=self.fees, reason=self.reason, strategy=self.strategy,
            reject_reason=self.reject_reason, client_order_id=self.client_order_id,
            created_at=_aware(self.created_at), updated_at=_aware(self.updated_at),
        )

    @classmethod
    def from_model(cls, o: Order) -> "OrderRow":
        return cls(
            id=o.id, venue=o.venue, venue_order_id=o.venue_order_id, symbol=o.symbol, market=o.market.value,
            currency=o.currency, side=o.side.value, qty=o.qty, filled_qty=o.filled_qty,
            order_type=o.order_type.value, limit_price=o.limit_price, stop_price=o.stop_price, tif=o.tif.value,
            status=o.status.value, avg_fill_price=o.avg_fill_price, fees=o.fees, reason=o.reason,
            strategy=o.strategy, reject_reason=o.reject_reason, client_order_id=o.client_order_id,
            created_at=o.created_at, updated_at=o.updated_at,
        )


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(36), index=True)
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    def to_model(self) -> Fill:
        return Fill(id=self.id, order_id=self.order_id, venue=self.venue, symbol=self.symbol,
                    market=Market(self.market), side=Side(self.side), qty=self.qty, price=self.price,
                    fee=self.fee, ts=_aware(self.ts))


class PositionRow(Base):
    __tablename__ = "positions"
    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    market: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_model(self) -> Position:
        return Position(venue=self.venue, symbol=self.symbol, market=Market(self.market), currency=self.currency,
                        qty=self.qty, avg_price=self.avg_price, realized_pnl=self.realized_pnl,
                        ts=_aware(self.updated_at))


class AccountRow(Base):
    __tablename__ = "accounts"
    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    cash: Mapped[float] = mapped_column(Float)
    starting_cash: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquityRow(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(8), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cash: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)


class JournalRow(Base):
    __tablename__ = "journal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    def to_model(self) -> JournalEntry:
        return JournalEntry(id=self.id, ts=_aware(self.ts), kind=self.kind, venue=self.venue, symbol=self.symbol,
                            order_id=self.order_id, text=self.text, data=self.data)


class ThesisRow(Base):
    __tablename__ = "theses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    venue: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8), default="long")
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    size_notional: Mapped[float] = mapped_column(Float)
    stop_pct: Mapped[float] = mapped_column(Float)
    target_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    entry_order_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_order_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    realized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    def to_model(self) -> Thesis:
        return Thesis(id=self.id, created_at=_aware(self.created_at), updated_at=_aware(self.updated_at), venue=self.venue,
                      symbol=self.symbol, market=Market(self.market), currency=self.currency, direction=self.direction,
                      text=self.text, confidence=self.confidence, size_notional=self.size_notional, stop_pct=self.stop_pct,
                      target_pct=self.target_pct, expires_at=_aware(self.expires_at), status=ThesisStatus(self.status),
                      entry_order_id=self.entry_order_id, qty=self.qty, entry_price=self.entry_price,
                      exit_order_id=self.exit_order_id, exit_price=self.exit_price, closed_at=_aware(self.closed_at),
                      close_reason=self.close_reason, realized_pnl=self.realized_pnl, tags=self.tags or [])


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{self.db_path}"
        else:
            url = "sqlite:///:memory:"
        self.engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL")) if self.db_path != ":memory:" else None
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def session(self) -> Session:
        return self._session()

    # ---- orders -----------------------------------------------------------
    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def save_order(self, order: Order) -> Order:
        order.updated_at = utcnow()
        with self.session() as s:
            row = s.get(OrderRow, order.id)
            if row is None:
                s.add(OrderRow.from_model(order))
            else:
                for k, v in OrderRow.from_model(order).__dict__.items():
                    if not k.startswith("_"):
                        setattr(row, k, v)
            s.commit()
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        with self.session() as s:
            row = s.get(OrderRow, order_id)
            if row is None:
                # allow prefix / venue id lookup
                row = s.scalar(select(OrderRow).where((OrderRow.venue_order_id == order_id) | (OrderRow.client_order_id == order_id)))
            return row.to_model() if row else None

    def list_orders(self, venue: Optional[str] = None, symbol: Optional[str] = None, status: Optional[Iterable[str]] = None,
                    open_only: bool = False, limit: int = 100, since: Optional[datetime] = None) -> list[Order]:
        with self.session() as s:
            q = select(OrderRow).order_by(OrderRow.created_at.desc()).limit(limit)
            if venue:
                q = q.where(OrderRow.venue == venue)
            if symbol:
                q = q.where(OrderRow.symbol == symbol)
            if status:
                q = q.where(OrderRow.status.in_(list(status)))
            if open_only:
                q = q.where(OrderRow.status.in_([OrderStatus.NEW.value, OrderStatus.ACCEPTED.value, OrderStatus.PARTIALLY_FILLED.value]))
            if since:
                q = q.where(OrderRow.created_at >= since)
            return [r.to_model() for r in s.scalars(q)]

    # ---- fills ------------------------------------------------------------
    def save_fill(self, fill: Fill) -> Fill:
        with self.session() as s:
            s.add(FillRow(id=fill.id, order_id=fill.order_id, venue=fill.venue, symbol=fill.symbol,
                          market=fill.market.value, side=fill.side.value, qty=fill.qty, price=fill.price,
                          fee=fill.fee, ts=fill.ts))
            s.commit()
        return fill

    def list_fills(self, venue: Optional[str] = None, symbol: Optional[str] = None, since: Optional[datetime] = None,
                   limit: int = 500) -> list[Fill]:
        with self.session() as s:
            q = select(FillRow).order_by(FillRow.ts.desc()).limit(limit)
            if venue:
                q = q.where(FillRow.venue == venue)
            if symbol:
                q = q.where(FillRow.symbol == symbol)
            if since:
                q = q.where(FillRow.ts >= since)
            return [r.to_model() for r in s.scalars(q)]

    # ---- paper positions / accounts --------------------------------------
    def get_position(self, venue: str, symbol: str) -> Optional[Position]:
        with self.session() as s:
            row = s.get(PositionRow, (venue, symbol))
            return row.to_model() if row else None

    def upsert_position(self, pos: Position) -> None:
        with self.session() as s:
            row = s.get(PositionRow, (pos.venue, pos.symbol))
            if row is None:
                row = PositionRow(venue=pos.venue, symbol=pos.symbol, market=pos.market.value, currency=pos.currency)
                s.add(row)
            row.qty = pos.qty
            row.avg_price = pos.avg_price
            row.realized_pnl = pos.realized_pnl
            row.updated_at = utcnow()
            s.commit()

    def list_positions(self, venue: Optional[str] = None, market: Optional[Market] = None, include_flat: bool = False) -> list[Position]:
        with self.session() as s:
            q = select(PositionRow)
            if venue:
                q = q.where(PositionRow.venue == venue)
            if market:
                q = q.where(PositionRow.market == market.value)
            rows = [r.to_model() for r in s.scalars(q)]
            return rows if include_flat else [p for p in rows if abs(p.qty) > 1e-12]

    def get_account(self, venue: str, market: Market) -> Optional[Account]:
        with self.session() as s:
            row = s.get(AccountRow, (venue, market.value))
            if not row:
                return None
            return Account(venue=row.venue, market=Market(row.market), currency=row.currency, cash=row.cash,
                           starting_cash=row.starting_cash, realized_pnl=row.realized_pnl, ts=_aware(row.updated_at))

    def upsert_account(self, acct: Account) -> None:
        with self.session() as s:
            row = s.get(AccountRow, (acct.venue, acct.market.value))
            if row is None:
                row = AccountRow(venue=acct.venue, market=acct.market.value, currency=acct.currency,
                                 cash=acct.cash, starting_cash=acct.starting_cash or acct.cash)
                s.add(row)
            row.cash = acct.cash
            row.realized_pnl = acct.realized_pnl
            row.updated_at = utcnow()
            s.commit()

    def reset_paper(self, venue: str = "paper") -> None:
        with self.session() as s:
            for tbl in (OrderRow, FillRow, PositionRow, AccountRow, EquityRow):
                s.execute(tbl.__table__.delete().where(tbl.venue == venue))
            s.execute(JournalRow.__table__.delete().where(JournalRow.venue == venue))
            s.commit()

    # ---- equity -----------------------------------------------------------
    def add_equity_point(self, p: EquityPoint) -> None:
        with self.session() as s:
            s.add(EquityRow(venue=p.venue, market=p.market.value, ts=p.ts, cash=p.cash,
                            positions_value=p.positions_value, equity=p.equity))
            s.commit()

    def equity_curve(self, venue: str, market: Market, limit: int = 2000) -> list[EquityPoint]:
        with self.session() as s:
            q = select(EquityRow).where(EquityRow.venue == venue, EquityRow.market == market.value) \
                .order_by(EquityRow.ts.desc()).limit(limit)
            rows = list(s.scalars(q))
            rows.reverse()
            return [EquityPoint(venue=r.venue, market=Market(r.market), ts=_aware(r.ts), cash=r.cash,
                                positions_value=r.positions_value, equity=r.equity) for r in rows]

    def equity_at_day_start(self, venue: str, market: Market) -> Optional[float]:
        start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self.session() as s:
            q = select(EquityRow).where(EquityRow.venue == venue, EquityRow.market == market.value, EquityRow.ts < start) \
                .order_by(EquityRow.ts.desc()).limit(1)
            row = s.scalar(q)
            if row:
                return row.equity
            q = select(EquityRow).where(EquityRow.venue == venue, EquityRow.market == market.value) \
                .order_by(EquityRow.ts.asc()).limit(1)
            row = s.scalar(q)
            return row.equity if row else None

    # ---- journal ----------------------------------------------------------
    def journal(self, entry: JournalEntry) -> JournalEntry:
        with self.session() as s:
            row = JournalRow(ts=entry.ts, kind=entry.kind, venue=entry.venue, symbol=entry.symbol,
                             order_id=entry.order_id, text=entry.text, data=entry.data)
            s.add(row)
            s.commit()
            entry.id = row.id
        return entry

    def list_journal(self, limit: int = 100, kind: Optional[str] = None, symbol: Optional[str] = None,
                     since: Optional[datetime] = None) -> list[JournalEntry]:
        with self.session() as s:
            q = select(JournalRow).order_by(JournalRow.ts.desc()).limit(limit)
            if kind:
                q = q.where(JournalRow.kind == kind)
            if symbol:
                q = q.where(JournalRow.symbol == symbol)
            if since:
                q = q.where(JournalRow.ts >= since)
            return [r.to_model() for r in s.scalars(q)]

    # ---- theses -----------------------------------------------------------
    def save_thesis(self, t: Thesis) -> Thesis:
        t.updated_at = utcnow()
        with self.session() as s:
            row = s.get(ThesisRow, t.id)
            if row is None:
                row = ThesisRow(id=t.id, created_at=t.created_at)
                s.add(row)
            for k in ("updated_at", "venue", "symbol", "currency", "direction", "text", "confidence", "size_notional", "stop_pct",
                      "target_pct", "expires_at", "entry_order_id", "qty", "entry_price", "exit_order_id", "exit_price",
                      "closed_at", "close_reason", "realized_pnl", "tags"):
                setattr(row, k, getattr(t, k))
            row.market = t.market.value
            row.status = t.status.value
            s.commit()
        return t

    def get_thesis(self, thesis_id: str) -> Optional[Thesis]:
        with self.session() as s:
            row = s.get(ThesisRow, thesis_id)
            if row is None:
                row = s.scalar(select(ThesisRow).where(ThesisRow.id.like(f"{thesis_id}%")))
            return row.to_model() if row else None

    def list_theses(self, statuses: Optional[Iterable[str]] = None, venue: Optional[str] = None, limit: int = 200) -> list[Thesis]:
        with self.session() as s:
            q = select(ThesisRow).order_by(ThesisRow.created_at.desc()).limit(limit)
            if statuses:
                q = q.where(ThesisRow.status.in_(list(statuses)))
            if venue:
                q = q.where(ThesisRow.venue == venue)
            return [r.to_model() for r in s.scalars(q)]

    def orders_in_last(self, seconds: int, venue: Optional[str] = None) -> int:
        since = utcnow() - timedelta(seconds=seconds)
        return len(self.list_orders(venue=venue, since=since, limit=10_000))
