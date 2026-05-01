"""Aggregate ./official_runs/*/run.json into docs/data/leaderboard.json
and update README's TOP_5 table.

Usage:
    python scripts/update_leaderboard.py [--include-unofficial]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hab.cli.export import verify_export
from hab.analytics.leaderboard import LeaderboardGenerator


def validate_run_policy(run_json: dict, source: Path) -> list[str]:
    errors: list[str] = []
    security = run_json.get("agent_security")
    if not isinstance(security, dict):
        errors.append("missing agent_security metadata")
    elif security.get("unsafe_permissions"):
        errors.append("unsafe agent permissions are not allowed on public leaderboards")
    privacy = run_json.get("privacy") or {}
    if privacy.get("contains_private_workspaces") is not False:
        errors.append("run export must not include private workspaces")
    if run_json.get("schema_version") != "hab.run.v1":
        errors.append(f"unsupported run schema in {source}")
    return errors


def update_readme_top5(data: dict, readme_path: Path) -> None:
    if not readme_path.exists():
        return
    content = readme_path.read_text()
    start_marker = "<!-- LEADERBOARD_START -->"
    end_marker = "<!-- LEADERBOARD_END -->"
    if start_marker not in content or end_marker not in content:
        return

    rows = [
        "| Rank | Model | Elo | Skill BB/100 | Harness | Hands |",
        "|------|-------|-----|--------------|---------|-------|",
    ]
    for entry in data["entries"][:5]:
        skill_pt = entry["skill_bb_per_100"]["point"]
        if skill_pt is None:
            skill_text = "—"
        else:
            sign = "+" if skill_pt >= 0 else ""
            skill_text = f"{sign}{skill_pt:.1f}"
        harness_score = (entry.get("harness") or {}).get("harness_score")
        harness_text = "—" if harness_score is None else f"{harness_score:.1f}"
        rows.append(
            f"| {entry['rank']} | `{entry['model']}` | {int(entry['elo'])} | "
            f"{skill_text} | {harness_text} | {entry['hands_played']:,} |"
        )
    if len(rows) == 2:
        rows.append("| _no eligible runs yet_ | | | | | |")

    new_table = "\n".join(rows)
    pre, _, rest = content.partition(start_marker)
    _, _, post = rest.partition(end_marker)
    new_content = f"{pre}{start_marker}\n{new_table}\n{end_marker}{post}"
    readme_path.write_text(new_content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="official_runs", type=Path)
    parser.add_argument("--output", default="docs/data/leaderboard.json", type=Path)
    parser.add_argument("--readme", default="README.md", type=Path)
    parser.add_argument("--include-unofficial", action="store_true",
                        help="Skip eligibility filtering")
    args = parser.parse_args()

    runs_dir: Path = args.runs_dir
    runs: list[dict] = []
    if runs_dir.exists():
        for p in sorted(runs_dir.glob("*/run.json")):
            errors = verify_export(p.parent)
            if errors:
                joined = "\n".join(f"  - {e}" for e in errors)
                raise SystemExit(f"Checksum verification failed for {p.parent}:\n{joined}")
            run_json = json.loads(p.read_text())
            policy_errors = validate_run_policy(run_json, p)
            if policy_errors:
                joined = "\n".join(f"  - {e}" for e in policy_errors)
                raise SystemExit(f"Run policy validation failed for {p}:\n{joined}")
            runs.append(run_json)

    gen = LeaderboardGenerator()
    for r in runs:
        gen.ingest_session(r)

    data = gen.write(args.output, only_eligible=not args.include_unofficial)
    print(f"✅ Wrote {args.output} with {len(data['entries'])} entries")

    update_readme_top5(data, args.readme)
    print(f"✅ Updated TOP-5 in {args.readme}")


if __name__ == "__main__":
    main()
