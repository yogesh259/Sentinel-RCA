import re
from pathlib import Path

from src.agents.context import describe_findings
from src.llm.client import structured
from src.models.report import RCAReport, ReviewResult, SuspectLocation
from src.models.state import SentinelState
from src.services.repo_cache import RepoError, apply_patch, run_git

SHA = re.compile(r"^[0-9a-fA-F]{4,40}$")


def reviewer_node(state: SentinelState) -> dict:
    """Check the report is grounded: cheap code checks first, then one LLM check against the evidence."""
    report = state["report"]
    problems = check_citations(Path(state["repo_path"]), report)
    if problems:
        return {"review": ReviewResult(grounded=False, problems=problems)}
    review = structured(
        "reviewer",
        ReviewResult,
        report=report.model_dump_json(indent=2),
        findings=describe_findings(state["findings"]),
    )
    return {"review": review}


def check_citations(repo: Path, report: RCAReport) -> list[str]:
    """Problems an LLM can't argue with: missing files, impossible lines, unknown commits, bad patches."""
    problems = [problem for suspect in report.suspects if (problem := check_location(repo, suspect))]
    if report.introduced_by and not commit_exists(repo, report.introduced_by):
        problems.append(f"introduced_by {report.introduced_by} is not a commit in this repository")
    if report.suggested_patch:
        try:
            apply_patch(repo, report.suggested_patch, check_only=True)
        except RepoError as exc:
            problems.append(f"suggested_patch does not apply: {exc}")
    return problems


def check_location(repo: Path, suspect: SuspectLocation) -> str | None:
    path = (repo / suspect.file).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        return f"{suspect.file} does not exist in the repository"
    total = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    if not 1 <= suspect.start_line <= suspect.end_line <= total:
        return f"{suspect.file}:{suspect.start_line}-{suspect.end_line} is out of range (file has {total} lines)"
    return None


def commit_exists(repo: Path, sha: str) -> bool:
    if not SHA.match(sha):
        return False
    try:
        run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except RepoError:
        return False
