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
