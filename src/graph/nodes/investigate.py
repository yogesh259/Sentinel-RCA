"""Investigate node: the pipeline's one agentic step.

Retrieve-then-judge: deterministic code ranks candidate commits from git blame and
history (src/services/rca_evidence.py); one deep-RCA LLM call judges them; code then
validates the verdict so the model can never point at a commit it wasn't shown.
"""

import logging

from src.graph.policy import detect_prompt_injection
from src.graph.state import RCAState
from src.schemas.rca import RCAVerdict
from src.services import llm_router
from src.services.rca_evidence import Evidence, gather_evidence
from src.services.rca_prompts import build_rca_messages

logger = logging.getLogger("sentinel-rca.nodes.investigate")


def abstention(root_cause: str) -> RCAVerdict:
    """A verdict that names no suspect, used when there is nothing to judge."""
    return RCAVerdict(suspect_sha=None, confidence="low", severity="medium", root_cause=root_cause)


def enforce_candidate_guardrails(
    verdict: RCAVerdict, candidate_shas: list[str]
) -> tuple[RCAVerdict, list[str]]:
    """Reject any SHA the model was not shown; return the cleaned verdict and audit events.

    Short SHAs are expanded so a model quoting `4b66e57` still matches its candidate.
    """
    events: list[str] = []

    def expand(sha: str | None) -> str | None:
        if not sha:
            return None
        matches = [c for c in candidate_shas if c.startswith(sha.lower())]
        return matches[0] if len(matches) == 1 else None

    updates: dict = {}
    suspect = expand(verdict.suspect_sha)
    if verdict.suspect_sha and suspect is None:
        events.append(f"Discarded suspect_sha {verdict.suspect_sha!r}: not a retrieved candidate.")
    updates["suspect_sha"] = suspect

    kept = [e.model_copy(update={"sha": expand(e.sha)}) for e in verdict.evidence if expand(e.sha)]
    if len(kept) < len(verdict.evidence):
        events.append(f"Dropped {len(verdict.evidence) - len(kept)} evidence item(s) citing unknown commits.")
    updates["evidence"] = kept

    if suspect is None and verdict.confidence != "low":
        events.append("Confidence lowered to 'low' because no valid suspect commit remains.")
        updates["confidence"] = "low"

    return verdict.model_copy(update=updates), events


async def judge_evidence(state: RCAState, evidence: Evidence) -> tuple[RCAVerdict, list[dict], list[str]]:
    """Ask the deep-RCA model to pick a suspect among the ranked candidates."""
    messages = build_rca_messages(
        state.get("exception_type", ""),
        state.get("exception_message", ""),
        state.get("raw_trace", ""),
        evidence,
    )
    verdict, calls = await llm_router.complete_structured("deep_rca", messages, RCAVerdict)
    verdict, events = enforce_candidate_guardrails(verdict, [c.sha for c in evidence.candidates])
    return verdict, [c.usage() for c in calls], events


def verdict_update(verdict: RCAVerdict, evidence: Evidence) -> dict:
    """Map a verdict onto the state fields consumed by the gate and tracker."""
    return {
        "candidates": [c.to_state() for c in evidence.candidates],
        "suspect_sha": verdict.suspect_sha or "",
        "confidence": verdict.confidence,
        "root_cause": verdict.root_cause,
        "mechanism": verdict.mechanism,
        "evidence": [e.model_dump() for e in verdict.evidence],
        "suggested_severity": verdict.severity,
        "patch_summary": verdict.fix_suggestion,
    }


async def investigate_git_node(state: RCAState) -> dict:
    """Node 3: correlate the failing frames with git history and judge the root cause."""
    if state.get("is_duplicate"):
        return {
            "root_cause": "Duplicate of previously tracked issue.",
            "suggested_severity": "low",
            "patch_summary": "",
        }

    # Don't spend tokens on (or expose the model to) a trace the gate will block anyway.
    if detect_prompt_injection(state.get("raw_trace", "")):
        verdict = abstention("Prompt-injection pattern in trace; analysis skipped.")
        return verdict_update(verdict, Evidence(candidates=[]))

    evidence = await gather_evidence(state.get("frames") or [])
    if not evidence.candidates:
        verdict = abstention("No commit in the repository history could be linked to the failing frames.")
        return verdict_update(verdict, evidence)

    try:
        verdict, llm_calls, events = await judge_evidence(state, evidence)
    except llm_router.LLMError as exc:
        logger.error("Deep RCA unavailable: %s", exc)
        update = verdict_update(abstention(f"LLM analysis unavailable: {exc}"), evidence)
        return {**update, "investigation_error": str(exc)}

    return {**verdict_update(verdict, evidence), "guardrail_events": events, "llm_calls": llm_calls}
