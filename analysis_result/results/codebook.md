# Prompt Clause Codebook

Labels are multi-source but represented as one primary category per clause in this first analysis pass.

| Category | Description |
| --- | --- |
| `identity_mission` | Agent identity, mission, role, and high-level task boundary. |
| `interaction_output` | Tone, response format, progress updates, and user-facing communication. |
| `planning_lifecycle` | Planning, todos, task lifecycle, completion and blocking states. |
| `software_engineering_workflow` | Code reading/editing, tests, git, builds, review, and repository workflow. |
| `tool_capability` | Tool names, descriptions, parameters, schemas, and capability surface. |
| `tool_use_policy` | Rules for when and how tools should be used. |
| `permissions_side_effects` | Approval, confirmation, destructive actions, external side effects. |
| `safety_security` | Safety, privacy, credential handling, dual-use and malicious request boundaries. |
| `environment_sandbox` | Filesystem, shell, OS, sandbox, workspace, network, and runtime environment. |
| `memory_context` | Memory, session state, summarization, context management. |
| `extensibility` | Skills, plugins, MCP, project instructions, customization. |
| `multi_agent` | Delegation, subagents, parallel agents, multi-session orchestration. |
| `reliability_verification` | Testing, validation, evidence, failure reporting, factuality. |
| `runtime_capture_artifact` | Synthetic task, volatile paths, dates, IDs, and capture-specific context. |
| `uncertain` | Insufficient evidence for a confident rule/model category. |

## Classifier Status

- Enabled: `False`
- Model: `qwen3.6-35ba3b`
- Base URL: `https://siflow-changliu.siflow.cn/siflow/changliu/f93ea1119a/qwen36-35ba3b/v1/8000/v1`
- Note: model classifier disabled; rule-only labels used

Model-assisted labels are auxiliary. They are cached in `classification_cache.jsonl` and should be audited before being used as strong evidence.
