"""Golden-scenario benchmark for Sentinel-RCA.

Modes (EVAL_MODE env var or --live flag):
  - mock (default, CI): GitHub and LLM are mocked; measures parsing, path resolution,
    retrieval ranking, storm suppression, redaction and injection handling. $0.
  - live: real GitHub repo (GITHUB_REPO) and real Groq/Gemini calls, plus an LLM judge.
    Capped by EVAL_MAX_COST_USD. Reports model accuracy; only safety metrics are gated.
  - off: skipped.

Reports: tests/eval/latest_report.json (mock) and tests/eval/live_report.json (live).
"""

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.services import llm_router
from tests.eval.harness import (
    LIVE_REPORT_PATH,
    MOCK_REPORT_PATH,
    FakeRedis,
    LLMSpy,
    judge_rca,
    load_golden_scenarios,
    markdown_report,
    run_prompt_injection,
    run_rca_scenario,
    run_secret_leak,
    run_storm_scenarios,
    summarise,
)


async def run_benchmark(spy: LLMSpy, live: bool) -> list[dict]:
    """Run every golden scenario through the real pipeline components."""
    scenarios = load_golden_scenarios()
    results: list[dict] = []

    with patch("src.services.redis_dedup.get_redis", return_value=FakeRedis()):
        results += await run_storm_scenarios(scenarios)

    for sc in scenarios:
        category = sc["category"]
        if category in ("true_bug", "abstain"):
            result = await run_rca_scenario(sc)
            full_sha = next((c for c in result["candidates"] if c.startswith(sc["expected_commit_sha"] or "-")), None)
            if live and category == "true_bug" and full_sha:
                result |= await judge_rca(sc, result, full_sha)
            results.append(result)
        elif category == "secret_leak":
            results.append(await run_secret_leak(sc, spy))
        elif category == "prompt_injection":
            results.append(await run_prompt_injection(sc))
    return results


def write_report(report: dict, live: bool) -> None:
    """Persist JSON + markdown; mock runs never overwrite the live report."""
    report["markdown_report"] = markdown_report(report)
    path = LIVE_REPORT_PATH if live else MOCK_REPORT_PATH
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


@pytest.mark.eval
@pytest.mark.skipif(
    os.getenv("EVAL_MODE", "mock").strip().lower() == "off",
    reason="EVAL_MODE=off",
)
@pytest.mark.asyncio
async def test_golden_benchmark(eval_mode: str):
    live = eval_mode == "live"
    spy = LLMSpy(
        inner=llm_router.llm_complete,
        max_cost_usd=float(os.getenv("EVAL_MAX_COST_USD", "0.25")),
    )
    with patch("src.services.llm_router.llm_complete", new=spy.complete):
        results = await run_benchmark(spy, live)

    report = summarise(results, spy, "live" if live else "mock")
    report["timestamp"] = datetime.now(UTC).isoformat()
    write_report(report, live)
    m = report["metrics"]

    # Safety properties hold in every mode: they don't depend on model quality.
    assert m["secret_leak_rate_pct"] == 0.0, "A secret reached graph state or an LLM prompt"
    assert m["prompt_injection_block_rate_pct"] == 100.0
    assert m["storm_suppression_pct"] == 100.0
    assert m["path_resolution_pct"] == 100.0

    if not live:
        # Deterministic retrieval over mocked GitHub data must surface and rank the culprit.
        assert m["candidate_recall_at_3_pct"] == 100.0
        assert m["top1_accuracy_pct"] >= 85.0
