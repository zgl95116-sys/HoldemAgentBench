---
name: gto-reference
description: When and how to query gto_lookup and interpret its output.
---

# GTO Reference

## What `gto_lookup` returns

`gto_lookup` is a static preflop chart. Use it when you have no opponent history or want a baseline.

Scenarios available (MVP):
- `HU_SB_open` — heads-up, you're on the button (SB), open or fold.
- `6M_UTG_open` — 6-max UTG open or fold.
- `6M_BTN_open` — 6-max button open or fold.

## When to deviate from GTO

GTO ≈ "unexploitable but not maximally exploitative". Deviate when:

1. **Opponent leaks visible**: `opponent_database_query` returns confidence ≥ medium and stats are unbalanced.
2. **ICM / short stack**: GTO chart assumes 100bb. Push/fold dynamics change.
3. **Specific reads from notes**: `note_manager read` unearthed a tell.

## Don't deviate when

- Sample size is small (< 30 hands).
- Opponent is also a model — might know your deviation.
- Stack depth is normal and you have no read.

## Postflop

The MVP toolkit doesn't include solver-grade postflop GTO. Approximate:

- **Single-raised pot, 100bb**: c-bet 50–60% as PFR on dry boards, less on wet.
- **3-bet pot**: c-bet smaller (~33%) range-bet on most boards.
- Use `equity_calculator` against an estimated range from `range_analyzer`.
