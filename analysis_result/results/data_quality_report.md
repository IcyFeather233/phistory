# Data Quality Report

- Analysis date: 2026-07-15T03:12:48.674444+00:00
- Repository commit: `199034e1f7595fed179a3d0dda8c1bda338c6733`
- Complete snapshots included: 737
- Agents: 11
- Missing prompt files among included snapshots: 0
- Missing trace files among included snapshots: 0
- Optional model classifier: model classifier disabled; rule-only labels used

## Snapshots by Agent

| Agent | Snapshots | First Published | Last Published | Static Prompt Files |
| --- | ---: | --- | --- | ---: |
| claude-code | 366 | 2025-05-22 | 2026-07-14 | 31 |
| codex | 68 | 2026-01-09 | 2026-07-14 | 0 |
| antigravity | 16 | 2026-06-01 | 2026-07-13 | 0 |
| kimi-code | 44 | 2026-05-22 | 2026-07-14 | 0 |
| mimo | 5 | 2026-06-15 | 2026-07-07 | 0 |
| openclaw | 68 | 2026-01-30 | 2026-07-13 | 0 |
| hermes | 19 | 2026-03-24 | 2026-07-08 | 0 |
| kimi | 20 | 2026-01-27 | 2026-06-22 | 0 |
| opencode | 85 | 2026-04-08 | 2026-07-13 | 0 |
| pi | 29 | 2026-05-07 | 2026-07-09 | 0 |
| omp | 17 | 2026-07-02 | 2026-07-14 | 0 |

## Trace Parse Status

| Status | Count |
| --- | ---: |
| `ok` | 664 |
| `missing_body` | 73 |

## Notes

- `prompt.md` is normalized for human reading; `trace.jsonl` is raw request evidence.
- Request body structure differs across tap clients and providers, so unknown fields are counted rather than discarded.
- Static Claude Code prompt extraction is tracked separately and is not merged into runtime OPS statistics.
