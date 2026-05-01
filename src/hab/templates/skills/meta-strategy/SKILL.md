---
name: meta-strategy
description: How to make every poker decision in HAB. Read this every time you are awakened.
---

# Meta-Strategy: Decision Workflow

You are awakened to make a single decision. Time is finite (5 min hard cap). Be deliberate but quick.

## Step 1: Read state

```
cat game_view/current_state.json
cat game_view/hole_cards.json
```

Note: hand_id, street, board, pot, current_bet, legal_actions, your stack, opponents' stacks.

## Step 2: Pot weight + shot clock

You have a **90-second base clock per decision** plus a 180s session-wide time
bank. The prompt tells you exact remaining bank. Pace accordingly.

| Pot | Time target | Treatment |
|-----|-------------|-----------|
| < 10 BB | < 60s | **Quick** — preflop chart + position. ≤ 1 tool call. |
| 10–50 BB | < 90s | **Standard** — equity + pot_odds. ≤ 3 tool calls. |
| > 50 BB | up to 90s + bank | **Deep** — burn time-bank for thin spots. ≤ 6 tool calls. |

**Don't waste bank on routine spots.** Once it's gone, every decision must
finish in 90s flat or you're force-folded. Save it for big-pot turn/river.

## Step 3: Decide — preferred tool sequence

For postflop / non-trivial spots, follow this order:

1. **`range_analyzer`** — pass observed_vpip + action_sequence → get a parseable
   `estimated_range` string like `"TT+,AJs+,KQs,AKo"`.
2. **`equity_calculator`** — pass that range as `opponent_range`. **DO NOT** use
   the default `any_two` once villain has shown action — equity vs random is
   misleading by 10-30%.
3. **`pot_odds_calculator`** — pass equity from step 2 as `my_equity`. Returns
   `verdict: call|fold|marginal`. For bluffs also pass `bluff_size` + estimated
   `fold_equity`.

For preflop:
- `gto_lookup` returns mixed frequencies (e.g. `raise_freq: 0.85`). Pick the
  majority action; for borderline hands (0.4-0.6 freq) you can mix.
- For HU BB defense, use `gto_lookup` with scenario `HU_BB_vs_open` — returns
  `three_bet | call | fold` frequencies.

## Common mistake — AVOID

Calling `equity_calculator` with `opponent_range="random"` (the default) after
villain has raised preflop and barreled. Their range is much narrower than
random, your real equity is far below the number you'll get. **Always run
range_analyzer first**.

## Step 4: Write action

`actions/action.json`:

```json
{
  "hand_id": "<copy from current_state.hand_id>",
  "action": "fold|check|call|raise",
  "amount": <number or null>,
  "reason": "<one short sentence>",
  "tool_calls_used": ["equity_calculator", "pot_odds_calculator"]
}
```

Honesty: `tool_calls_used` MUST list every tool you actually called. Do not lie.

## Step 5: After hand_complete (if you have time)

If this hand was instructive about an opponent, `note_manager append` a quick note to `notes/opponents/<opponent_id>.md`. Future-you will read it.

## Hard rules

- Action type must be in `legal_actions`.
- For `call`, `amount` = the call increment from `legal_actions`.
- For `raise`, `amount` ∈ `[amount_min, amount_max]` (absolute "raise to" target).
- For `fold` and `check`, `amount` = null.
- Don't read or write outside this workspace.
