---
name: opponent-modeling
description: How to read opponents from VPIP/PFR/AF and update notes after each hand.
---

# Opponent Modeling

Without history you must play GTO baseline. After ~30 hands per opponent, exploit their tendencies.

## Stat thresholds (6-max NLHE, rough)

| Stat | Tight | Standard | Loose |
|------|-------|----------|-------|
| VPIP | < 20% | 20–28% | > 28% |
| PFR | < 14% | 14–22% | > 22% |
| 3-bet | < 5% | 5–9% | > 9% |
| AF (postflop) | < 2 | 2–4 | > 4 |

## Player archetypes

- **Nit** (VPIP < 16, PFR < 14): folds too much, bluff often. Continuation-bet relentlessly.
- **TAG** (VPIP 18–24, PFR 14–22): solid. Don't bluff into them; value bet thin.
- **LAG** (VPIP 25–32, PFR 22–28, AF > 3): aggressive. Trap with strong hands; light 3-bets work.
- **Maniac** (VPIP > 35, PFR > 30, AF > 4): bluffs constantly. Value bet wide; call down lighter.
- **Calling station** (VPIP > 30, AF < 1.5): never folds. Stop bluffing; value bet medium-strong.

## Process each decision

1. `opponent_database_query opponent_id=<id>` — get VPIP/PFR/3-bet/AF/WTSD.
2. If `confidence == "no_data"`, default to GTO via `gto_lookup`.
3. Otherwise classify archetype and adjust.

## After each hand

If something surprising happened — `note_manager append`:

```
note_manager(
  action="append",
  opponent_id="player_b",
  observation_type="bluff" | "value" | "tendency",
  content="3-bet 7-2o from button, then triple-barreled bluff. Maniac confirmed.",
  hand_id="h_00042",
)
```

Notes accumulate. Read them next time with `note_manager read`.
