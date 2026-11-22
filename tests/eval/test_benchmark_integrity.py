"""Golden dataset and published report integrity checks."""

import json
import re

import pytest

from tests.eval.harness import LIVE_REPORT_PATH, load_golden_scenarios

REQUIRED_FIELDS = {
    "true_bug": {"raw_trace", "expected_file_path", "expected_commit_sha", "expected_action"},
    "abstain": {"raw_trace", "expected_commit_sha", "expected_action"},
    "storm_duplicate": {"raw_trace"},
    "secret_leak": {"raw_trace", "disallowed_pattern"},
    "prompt_injection": {"raw_trace"},
}
EXPECTED_COUNTS = {
    "true_bug": 8,
    "abstain": 3,
    "storm_duplicate": 5,
    "secret_leak": 4,
    "prompt_injection": 3,
}


@pytest.mark.eval
class TestBenchmarkIntegrity:
    def test_golden_scenarios_schema(self):
        scenarios = load_golden_scenarios()
        ids = [s["id"] for s in scenarios]
        assert len(ids) == len(set(ids)), "Duplicate scenario ids"

        counts = {c: sum(s["category"] == c for s in scenarios) for c in EXPECTED_COUNTS}
        assert counts == EXPECTED_COUNTS

        for sc in scenarios:
            missing = REQUIRED_FIELDS[sc["category"]] - sc.keys()
            assert not missing, f"{sc['id']} missing {missing}"

    def test_expected_commits_are_hex_or_abstain(self):
        for sc in load_golden_scenarios():
            sha = sc.get("expected_commit_sha")
            if sc["category"] == "true_bug":
                assert sha and re.fullmatch(r"[0-9a-f]{7,40}", sha), sc["id"]
            if sc["category"] == "abstain":
                assert sha is None, sc["id"]

    @pytest.mark.skipif(not LIVE_REPORT_PATH.exists(), reason="No live report published yet")
    def test_published_live_report_is_honest(self):
        """A published live report must come from real LLM calls, not the mock."""
        report = json.loads(LIVE_REPORT_PATH.read_text(encoding="utf-8"))
        assert report["mode"] == "live"
        assert report["cost"]["llm_calls"] > 0
        assert "mock/deterministic" not in report["cost"]["models"]
