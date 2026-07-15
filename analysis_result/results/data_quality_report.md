# Data Quality Report

- Analysis date: 2026-07-15T07:02:40.704745+00:00
- Repository commit: `9912d0ca0f650be13d84b10bd04eca852bb9cfe8`
- Complete snapshots included: 743
- Agents: 11
- Missing prompt files among included snapshots: 0
- Missing trace files among included snapshots: 0
- Optional model classifier: model classifier disabled; rule-only labels used

## Snapshots by Agent

| Agent | Snapshots | First Published | Last Published | Static Prompt Files |
| --- | ---: | --- | --- | ---: |
| claude-code | 367 | 2025-05-22 | 2026-07-14 | 32 |
| codex | 68 | 2026-01-09 | 2026-07-14 | 0 |
| antigravity | 16 | 2026-06-01 | 2026-07-13 | 0 |
| kimi-code | 44 | 2026-05-22 | 2026-07-14 | 0 |
| mimo | 5 | 2026-06-15 | 2026-07-07 | 0 |
| openclaw | 68 | 2026-01-30 | 2026-07-13 | 0 |
| hermes | 19 | 2026-03-24 | 2026-07-08 | 0 |
| kimi | 20 | 2026-01-27 | 2026-06-22 | 0 |
| opencode | 87 | 2026-04-08 | 2026-07-14 | 0 |
| pi | 30 | 2026-05-07 | 2026-07-14 | 0 |
| omp | 19 | 2026-07-02 | 2026-07-14 | 0 |

## Trace Parse Status

| Status | Count |
| --- | ---: |
| `ok` | 668 |
| `missing_body` | 75 |

## Notes

- `prompt.md` is normalized for human reading; `trace.jsonl` is raw request evidence.
- Request body structure differs across tap clients and providers, so unknown fields are counted rather than discarded.
- Static Claude Code prompt extraction is tracked separately and is not merged into runtime OPS statistics.
