# T212 OpenAPI contract notes (re-verified 2026-06-12)

Bundle: `https://docs.trading212.com/_bundle/api.json` (openapi 3.0.1).
This resolves open question #1 in `spec/tasks/00-overview.md`.

## Drift vs spec §8.1

| Spec §8.1 | Bundle reality | Action |
|-----------|----------------|--------|
| `GET /equity/account/cash` | **Does not exist.** Cash ships inside `GET /equity/account/summary` (`cash.availableToTrade`, `cash.reservedForOrders`, `cash.inPies`) | `get_account_cash()` derives from the summary response (T07) |
| `GET /equity/orders/{id}`, `DELETE /equity/orders/{id}` | Present, `id` is **integer** | as planned |
| `POST /orders/market`, `/orders/stop`, `/orders/limit`, `/orders/stop_limit` | All present | as planned; stop support still capability-gated at runtime (§8.3) |
| `GET /equity/history/orders`, `/history/transactions` | Present, paginated `{items, nextPagePath}`, `limit` ≤ 50 | as planned (C10) |

## Other findings affecting later tasks

- **No min-quantity / fractional-support field** on `TradableInstrument`
  (open question #4 / T09). Fields: ticker, type, currencyCode, name,
  shortName, isin, addedOn, extendedHours, maxOpenQuantity,
  workingScheduleId. → T09 stores `min_qty = NULL` (unknown); sizing
  treats NULL as fractional-allowed with a 0.01-share floor, and the
  first REJECTED order for a too-small quantity will surface reality.
- **Order statuses** are richer than §8.4's list: LOCAL, UNCONFIRMED,
  CONFIRMED, NEW, CANCELLING, CANCELLED, PARTIALLY_FILLED, FILLED,
  REJECTED, REPLACING, REPLACED. The DB enum keeps our internal
  lifecycle; raw broker status is preserved in `orders.raw_response`.
- **Position P&L:** no flat P&L field; `walletImpact` object plus
  `currentPrice`/`averagePricePaid` — client computes P&L (C9 still
  holds: positions work as a quote fallback).
- Market order request body: `{ticker, quantity, extendedHours?}` —
  sells via negative quantity (C6 confirmed). Stop: `{ticker, quantity,
  stopPrice, timeValidity}`.
- Rate-limit examples per endpoint (bundle): summary 1/5s, instruments
  1/50s, positions 1/1s, orders-list 1/5s, market-order 50/60s,
  stop-order 1/2s, history 6/60s. Headers: `x-ratelimit-limit/-period/
  -remaining/-used/-reset`.
