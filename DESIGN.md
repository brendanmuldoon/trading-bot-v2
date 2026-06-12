# T212 Bot — UI Design Direction

**Decision date:** 2026-06-11
**Design file:** `designs/t212-ui-designs.pen` (open in Pencil; the MCP operates on the active editor)
**Spec:** `spec/trading-bot-specification.md` (§12.1 frontend pages)

## Chosen direction: B — Dark Trading Terminal

Bloomberg/TradingView-dense single screen: persistent top status strip, panel grid,
monospace numerals everywhere, bottom quote ticker. The whole spec is visible at once —
state, both circuit breakers, data age, API budget, stop mode, positions, ledgers,
normalized equity, per-strategy stats, decision feed, system events.

### Why B

- The owner "lives in" this UI (spec §1.4); a dense market-desk surface rewards daily,
  repeated use more than a calm admin shell.
- The status strip keeps every safety signal (state, DAY/DD breakers, stops mode, data
  freshness, rate-limit headroom) permanently on screen — no navigation needed to answer
  "is it safe?".
- Panel grid generalizes to N strategies (strategy stat panels are a repeating unit, not
  a fixed pair).

### Ruled out

- **A — Calm Ops Console** (light sidebar admin): cheapest to build, but safety-critical
  state reads too quiet, and the aesthetic undersells the product.
- **C — Decision-First Mission Control** (state band + breaker tiles + decision log as
  dominant region): strongest audit narrative; lost to B on density and desk feel.
  Its best ideas should be folded into B over time — especially the decision log's
  reason/context lines and the explicit breaker limits.

## Visual language (Direction B tokens)

| Token | Value |
|---|---|
| Background | `#0A0E13` |
| Panel surface | `#0F151D` |
| Panel border | `#1C2530` (inner hairlines `#141B24`) |
| Strip/ticker surface | `#0E141B` |
| Text primary | `#E6EDF3` |
| Text secondary | `#8B949E` |
| Text faint | `#586069` (axis/labels `#3A4451`) |
| Positive / running | `#4ADE80` (tag bg `#10301F`, tab-active bg `#13211A`) |
| Negative / destructive | `#F87171` |
| Warning (DEMO, DENY, STALE) | `#FBBF24` |
| Strategy: trend | `#60A5FA` (tag bg `#11253A`) |
| Strategy: meanrev | `#C084FC` (tag bg `#241A38`) |
| Info | `#60A5FA` |
| Radius | 2px chips/tags, 4px panels — near-sharp |
| Font: data/labels/headers | IBM Plex Mono (panel headers: 10.5px, letter-spaced, uppercase) |
| Font: prose (rare) | Inter |

### Layout grammar

- **Status strip (44px, top):** brand · DEMO/LIVE box · state block (RUNNING/PAUSED/HALTED)
  · DAY and DD breaker readouts with limits · spacer · data age · API budget · stop mode ·
  PAUSE (red outline). Always present on every page.
- **Tab row (36px):** DASHBOARD / TRADES / STRATEGIES / LOG, active = green on `#13211A`;
  right-aligned next-signal countdown.
- **Body:** 10px padding/gutters; panels = `#0F151D` with 1px `#1C2530` border, header bar
  with bottom border. Dashboard grid: left 360px (positions, account), center fluid
  (equity, strategy panels), right 330px (decisions, events).
- **Ticker (30px, bottom):** universe quotes, green/red deltas.
- Charts: normalized-to-100 equity lines — total `#4ADE80` (2px), per-strategy in strategy
  colors (1.4px), grid `#161D27`, subtle green area fill under total.
- Tags are bordered mono chips: FILL/HELD/DENY/STALE/INFO in semantic colors.

### Do / don't

- Numerals are always mono; align right in tables.
- Never hardcode two strategies — strategy panels, legends, and exposure bars repeat per
  enabled strategy (wrap to grid at N>3).
- Safety state escalates loudly: PAUSED turns the state block amber; HALTED turns it red
  and the strip gains an acknowledge-to-resume affordance.
- Empty ≠ blank: a no-trade day still shows hourly HELD entries in the decision feed.
- No decorative gradients/shadows; depth comes from surface steps and hairlines.

## Edge & error cases to design/build (carried from ideation)

1. **DATA_OUTAGE:** equity/chart panel enters degraded "monitor-only" mode (banner; stop
   checks fall back to T212 position P&L per spec §7).
2. **Rate-limit pressure:** API readout in the strip turns amber <20% headroom, red on 429
   backoff — before failures, not after.
3. **N>3 strategies:** strategy panel row wraps to a grid; ticker/legends stay legible.
4. **Market closed / machine slept:** ticker and countdown switch to watch-mode copy
   ("MARKET CLOSED — OPENS 09:30 ET"); reconcile-on-wake surfaces as an event.
5. **HALTED state:** full strip turns red, resume requires explicit acknowledgment dialog
   (spec §9.3); UNCONFIGURED replaces body with setup instructions.
6. **Reconciliation diff / manual trades:** `manual` ledger rows appear visually distinct
   and excluded from strategy stats; RECONCILE_DIFF raises a WARN event + strip indicator.

## Open questions

- Stop-mode fallback (bot-side ⚠️) styling: how loud in the strip?
- Decision feed entries: adopt Direction C's two-line reason + mono context format? (leaning yes)
- Responsive behavior: terminal grid below ~1100px — stack panels or horizontal scroll?

## Next steps

1. Design remaining pages in `t212-ui-designs.pen`: TRADES (expandable history table),
   STRATEGIES (N-up comparison + normalized curves), LOG (decisions + events with filters).
2. Design state variants: PAUSED, HALTED, UNCONFIGURED, DATA_OUTAGE, market-closed.
3. Then map tokens to the React build (Tailwind config + recharts theme) when E9 starts.
