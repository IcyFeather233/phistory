from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis_result" / "src"))

from run_all import classify_rule, normalize_text, split_sentence_clause


def test_normalize_text_replaces_capture_artifacts():
    text = "Today's date is 2026-07-14. Reply with one short sentence."

    assert "$DATE" in normalize_text(text)
    assert "$SYNTHETIC_TASK" in normalize_text(text)


def test_rule_classifier_permissions():
    category, method = classify_rule("Ask before deleting files or making irreversible changes.", "instruction", "")

    assert category == "permissions_side_effects"
    assert method == "rule"


def test_sentence_split_keeps_short_clause_intact():
    clauses = split_sentence_clause("Use tests to verify the change before reporting completion.", 12)

    assert clauses == [(12, "Use tests to verify the change before reporting completion.")]
