import operator
from typing import Annotated, TypedDict

from src.models.finding import Finding
from src.models.plan import Plan
from src.models.report import Decision, RCAReport, ReviewResult
from src.models.trace import ParsedTrace


class SentinelState(TypedDict):
    repo_url: str
    base_commit: str | None  # evals pin the commit to analyse; None = default branch
    raw_trace: str
    repo_path: str | None
    trace: ParsedTrace | None
    plan: Plan | None
    findings: Annotated[list[Finding], operator.add]  # locator and history append in parallel
    report: RCAReport | None
    review: ReviewResult | None
    retries: int
    decision: Decision | None
    pr_url: str | None
    error: str | None
