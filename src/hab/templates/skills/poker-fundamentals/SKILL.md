---
name: poker-fundamentals
description: Core NLHE concepts — pot odds, equity, position, ranges, bet sizing.
---

# Poker Fundamentals

## Position

Late position (button > cutoff > hijack > MP > UTG > blinds in 6-max) is worth significant value. You see opponents' actions before deciding. Open wider in late position.

## Pot odds

Required equity to call profitably = `bet_to_call / (pot + bet_to_call)`.

Example: pot 100, bet 50 → need 50/150 ≈ 33% equity.

Use the `pot_odds_calculator` tool — it returns the verdict given your equity estimate.

## Equity

Your win probability vs opponent's range. Compute with `equity_calculator`:

- Use `opponent_range="random"` only when you have no info.
- Otherwise pre-estimate via `range_analyzer` and pass narrower assumption.

## Hand strength tiers (postflop)

- **Made hands**: top pair+ usually worth value; sets/two pair near-nuts.
- **Strong draws**: 2-pair-or-better outs, OESD + flush draw, set + flush draw.
- **Weak draws**: gutshots alone, bottom pair.
- **Air**: high cards, no pair, no draw.

## Bet sizing intuition

- C-bet flop: 33–66% pot in HU/multiway dry boards.
- Value bet river: 50–100% pot if villain has many bluff catchers.
- Bluff river: same size as value bets to mask intent.

## Stack-to-pot ratio (SPR)

- SPR < 4: commit with top pair / overpair.
- SPR 4–10: standard play, draws have OK implied odds.
- SPR > 10: implied odds matter; suited connectors and small pairs gain.

## Common leaks

- Calling 3-bets out of position with offsuit broadways.
- Slowplaying overpairs on wet boards.
- Bluffing into stations.
- Donk-leading flop without a plan for turn/river.
