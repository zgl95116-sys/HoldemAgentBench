---
name: Bug report
about: Something isn't working
title: '[bug] '
labels: bug
---

**What happened**
A clear and concise description.

**Reproduction**
Ideally a `mock://` matchup so we don't need API keys:

```bash
hab run quickstart --models mock://always-fold,mock://always-call --hands 10
```

If the bug only reproduces with real models, list the models and run command (redact any keys).

**Expected behavior**

**Logs**
Attach the relevant slice of `decision_log.jsonl`, `harness_metrics.json`, or stderr. Trim to the smallest reproducer you can.

**Environment**
- OS:
- Python version:
- HAB commit / version:
- Runtime: `claude-code-persistent` / `claude-code` / `openrouter`
- `claude --version` (if applicable):
