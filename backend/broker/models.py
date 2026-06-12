"""Typed response models for the T212 client (shapes per the OpenAPI
bundle — see docs/t212-contract-notes.md)."""

from pydantic import BaseModel, ConfigDict, Field


class Cash(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    available_to_trade: float = Field(alias="availableToTrade")
    in_pies: float = Field(alias="inPies", default=0.0)
    reserved_for_orders: float = Field(alias="reservedForOrders", default=0.0)


class Investments(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_value: float = Field(alias="currentValue")
    total_cost: float = Field(alias="totalCost")
    realized_profit_loss: float = Field(alias="realizedProfitLoss")
    unrealized_profit_loss: float = Field(alias="unrealizedProfitLoss")


class AccountSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    currency: str
    total_value: float = Field(alias="totalValue")
    cash: Cash
    investments: Investments


class InstrumentRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticker: str
    currency: str | None = None
    name: str | None = None


class BrokerPosition(BaseModel):
    """GET /equity/positions item. P&L computed — the bundle exposes no
    flat P&L field (docs/t212-contract-notes.md)."""

    model_config = ConfigDict(populate_by_name=True)

    instrument: InstrumentRef
    quantity: float
    average_price: float = Field(alias="averagePricePaid")
    current_price: float = Field(alias="currentPrice")

    @property
    def ticker(self) -> str:
        return self.instrument.ticker

    @property
    def unrealized_pnl(self) -> float:
        return self.quantity * (self.current_price - self.average_price)


class BrokerOrder(BaseModel):
    """Order response shape (signed wire quantity preserved here only —
    the public API speaks side + positive qty)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    ticker: str
    quantity: float  # signed, as on the wire
    filled_quantity: float = Field(alias="filledQuantity", default=0.0)
    side: str
    status: str
    type: str
    stop_price: float | None = Field(alias="stopPrice", default=None)
    created_at: str | None = Field(alias="createdAt", default=None)

    @property
    def abs_quantity(self) -> float:
        return abs(self.quantity)

    @property
    def abs_filled_quantity(self) -> float:
        return abs(self.filled_quantity)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("FILLED", "CANCELLED", "REJECTED", "REPLACED")
