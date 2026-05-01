# HoldemAgentBench: Scoring Methodology

**Version:** 1.1
**Last Updated:** 2026-04-29

## 1. Overview

HoldemAgentBench (HAB) reports poker performance and harness reliability.
Poker performance uses a three-layer scoring system:

1. **Raw BB/100** — actual win rate, transparent and verifiable.
2. **Skill BB/100** — variance-reduced via Duplicate Poker; reflects pure technical skill.
3. **Elo Rating** — comprehensive ranking that accounts for opponent strength.

The official leaderboard is sorted by Elo, but displays poker metrics and
harness reliability metrics. Harness score is not a poker strength metric; it
measures whether the model can operate inside the full agent environment.

## 2. Game Format

- **Variant:** No-Limit Texas Hold'em
- **Default preset:** `daily-bench` (6-max cash, $1/$2 blinds, 200 BB starting stack)
- **Decision timeout:** per-action shot clock + time bank (timeout = fold)
- **Max tool calls per decision:** soft target ≤ 10; hard cap none today, monitored
- **Agent runtimes:** `claude-code-persistent` for the core benchmark,
  `claude-code` for legacy one-shot compatibility, and `openrouter` for
  non-core fast comparison/debugging.
- **Claude Code effort:** official runs default to `--claude-effort low` so
  every model is evaluated under the same timed-agent budget.

## 3. Harness Reliability Score

Each action request creates a public decision record in `decision_log.jsonl`.
The record contains timing, output-protocol, tool-use, permission, and engine
validation fields. It does not contain raw model text, private workspace files,
or hidden hole cards.

`agent_runtime` is part of the exported run metadata. The official Claude Code
harness should use `claude-code-persistent`; scores from different runtimes
should not be mixed without labeling because they measure different harness
surfaces.

Harness score is a 0-100 reliability score:

```
0.40 * valid_action_rate
+ 0.20 * (1 - timeout_rate)
+ 0.15 * write_success_rate
+ 0.10 * (1 - protocol_error_rate)
+ 0.10 * latency_score
+ 0.05 * (1 - permission_error_rate)
```

Decision outcomes include `valid_action`, `invalid_action`, `timeout`,
`spawn_failed`, `no_output`, `bad_json`, and `error`. Invalid actions are
included in poker results via the engine fallback rule, but remain visible in
the harness score.

## 4. Layer 1: Raw BB/100

```
Raw BB/100 = (Total chips won / Big blind) × (100 / Total hands played)
```

We report 95% bootstrap CI with 10,000 resamples. Raw BB/100 has high variance — always interpret with CI.

## 5. Layer 2: Skill BB/100 (Duplicate Poker)

For each "template" (a fixed card sequence with a fixed seed):
1. Deal cards using the seed.
2. Run N rotations (N = number of players), each rotation places each player at each position once.
3. Reset table stacks to the preset starting stack for every rotation; score deltas still accumulate across the session.
4. Player's per-template skill delta = avg chips across rotations − template avg.
5. Skill BB/100 = mean(skill_deltas) / big_blind × 100; CI via bootstrap.

This zeroes out card-luck across the population that played the template.

If a run does not include duplicate templates, Skill BB/100 is reported as
unavailable rather than inferred from raw deltas.

`daily-bench` and `full-benchmark` enable template rotation in the runner.
Partial templates at the end of a session are excluded from Skill BB/100 until
all seats have played that template.

## 6. Layer 3: Elo Rating

- Initial rating: **1500**
- K factor: **32**

After each session:
1. Compare every pair of players.
2. Win/loss/draw decided by BB/100 with the **CI-overlap rule**: if 95% CIs overlap, it's a draw.
3. Update Elo using the standard formula.

## 7. Eligibility (official leaderboard)

| Requirement | Threshold |
|-------------|-----------|
| Minimum hands | 5,000 |
| Minimum sessions | 3 |
| Required preset | `daily-bench` or `full-benchmark` |
| Duplicate templates | Required for Skill BB/100 eligibility |
| Data completeness | Public hand history + decision telemetry; mucked/hidden hole cards excluded |
| Agent isolation | Minimal environment allowlist; unsafe Claude permissions disabled |
| All-runs rule | All official runs in a calendar month must be included |

## 8. Tier System

- 🏅 **Official** — run by the maintainers under standard conditions
- ✅ **Verified** — community submission, reproduced by maintainers
- ⚠️ **Unverified** — community submission, not yet reproduced
- 🚩 **Challenged** — under reproducibility challenge
- ❌ **Invalidated** — confirmed to violate methodology

## 9. Submission Process

1. Run a standard preset with unsafe agent permissions disabled.
2. Export public artifacts with `hab export-run <session_dir> --output official_runs/<session_id>`.
3. Data uploaded to public repo with `run.json`, per-hand JSON, and `checksums.json`.
4. Maintainers review public hand histories, decision summaries, tool call summaries, and final stacks for plausibility.
5. After approval, entry added with appropriate tier.

## 10. Reproducibility Challenges

Any user can challenge a leaderboard entry:
1. Run the same configuration with the same seed.
2. File a GitHub Issue with the discrepancy.
3. If confirmed, the original entry is downgraded.

## 11. Versioning

- **Patch** (1.0 → 1.1): bug fixes, no rescore.
- **Minor** (1.x → 1.y): new metrics, optional rescore.
- **Major** (1.x → 2.0): fundamental changes; previous scores archived.

## 12. Known Limitations

- Only NLHE 6-max is officially scored (HU runs are unranked).
- Cross-session learning (notes) is allowed but its quality isn't formally evaluated.
- Tool usage quality is reported in harness telemetry but not factored into Elo.
- The current runner records public showdown cards only; unrevealed mucked cards are
  intentionally omitted from public hand histories.
- The current runner does not enforce a kernel-level filesystem sandbox; official
  runs rely on environment minimization, tool allowlists, and export policy.

## 13. Open Data

Each official run under `official_runs/` contains:

- `run.json` — leaderboard-ready session summary, public hand histories, and
  decision summary.
- `hands/*.json` — one sanitized public hand record per hand.
- `decision_log.jsonl` source data is folded into public `run.json`; raw model
  text is not exported.
- `checksums.json` — SHA-256 manifest for reproducibility review.
- `agent_security` metadata — records the environment policy and whether unsafe
  agent permissions were used; public leaderboard updates reject unsafe runs.
