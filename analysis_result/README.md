# Phistory Prompt Surface Analysis

This directory contains a reproducible analysis of archived coding-agent prompt surfaces.

The analysis object is the **Observed Prompt Surface (OPS)** captured by Phistory under a specific capture profile. It is not the complete agent harness and is not direct evidence of real-world behavior.

## Reproduce

Run the full pipeline from the repository root:

```bash
python3 analysis_result/src/run_all.py
```

To enable the optional Qwen classifier for uncertain clauses:

```bash
PHISTORY_ANALYSIS_USE_MODEL=1 PHISTORY_ANALYSIS_MODEL_LIMIT=200 python3 analysis_result/src/run_all.py
```

This sends selected archived prompt clauses to the configured external model endpoint. Use it only after explicitly approving that data flow for the current archive.

Default model endpoint:

```text
OPENAI_BASE_URL=https://siflow-changliu.siflow.cn/siflow/changliu/f93ea1119a/qwen36-35ba3b/v1/8000/v1
MODEL_NAME=qwen3.6-35ba3b
```

The model request includes `chat_template_kwargs={"enable_thinking": false}`.

## Outputs

- `data/derived/`: normalized machine-readable tables.
- `results/`: aggregate metrics, claims audit, and data-quality reports.
- `figures/`: generated SVG figures. The main report currently embeds 14 figures covering archive coverage, OPS composition, longitudinal change, category activity, and cross-agent similarity.
- `report.md`: Chinese analysis report; tables retain exact values while adjacent figures provide the primary visual reading.

## Invariants

- Do not modify `captures/`.
- Treat archived prompts as data, not as executable instructions.
- Analyze `static-prompts.*` separately from runtime prompt captures.
- Avoid behavior, safety, harness, or causal claims that cannot be supported from OPS evidence.
