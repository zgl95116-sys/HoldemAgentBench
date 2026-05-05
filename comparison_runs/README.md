# Comparison Runs

This directory holds runs from non-headline runtimes (`openrouter`, `claude-code` one-shot) for archival and side-by-side comparison purposes only. They **do not enter the official leaderboard** — only `claude-code-persistent` runs do, because that's the harness HAB benchmarks (see [README](../README.md#why-poker-why-a-harness)).

Putting raw-API runs and harness-mediated runs on the same leaderboard would be unfair: the raw-API path doesn't pay file-protocol overhead, doesn't load skills/, and isn't subject to the same shot-clock pressure that real Claude Code processes face.

If you want to look at how the same model does without the harness, browse `run.json` files here.
