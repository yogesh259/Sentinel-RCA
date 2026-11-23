"""Opens a draft PR with the suggested patch. The only code that writes to GitHub."""

import hashlib
from pathlib import Path

from github import Auth, Github

from src.config.settings import get_settings
from src.models.report import Decision, RCAReport
from src.services.repo import RepoError, apply_patch, parse_repo_url, run_git


def pr_body(report: RCAReport, decision: Decision | None) -> str:
    """The PR description: the RCA, so a reviewer can judge the patch without rerunning Sentinel."""
    suspects = "\n".join(
        f"- `{s.file}` lines {s.start_line}-{s.end_line} ({s.confidence:.0%}): {s.reason}" for s in report.suspects
    )
    lines = [
        "## Root cause",
        report.root_cause,
        "",
        "## Suspects",
        suspects or "- none",
        "",
        f"**Introduced by:** {report.introduced_by or 'unknown'}  ",
        f"**Confidence:** {report.confidence:.0%}",
    ]
    if decision and decision.note:
        lines += ["", f"**Reviewer note:** {decision.note}"]
    lines += ["", "_Drafted by Sentinel and approved by a human before opening._"]
    return "\n".join(lines)


def branch_name(patch: str) -> str:
    return f"sentinel/{hashlib.sha256(patch.encode()).hexdigest()[:8]}"


def open_draft_pr(repo: Path, repo_url: str, report: RCAReport, body: str) -> str:
    """Branch, apply the patch, commit, push and open a draft PR. Returns the PR URL."""
    token = get_settings().github_token
    if token is None or not report.suggested_patch:
        raise RepoError("A GITHUB_TOKEN and a suggested patch are both required to open a PR.")
    secret = token.get_secret_value()
    owner, name = parse_repo_url(repo_url)
    branch = branch_name(report.suggested_patch)

    run_git(repo, "checkout", "--quiet", "-B", branch)
    apply_patch(repo, report.suggested_patch, check_only=False)
    run_git(
        repo,
        "-c",
        "user.name=Sentinel",
        "-c",
        "user.email=sentinel@users.noreply.github.com",
        "commit",
        "--quiet",
        "-m",
        report.title,
    )
    push_url = f"https://x-access-token:{secret}@github.com/{owner}/{name}.git"
    try:
        run_git(repo, "push", "--quiet", "--force", push_url, f"HEAD:refs/heads/{branch}")
    except RepoError as exc:
        raise RepoError(str(exc).replace(secret, "***")) from None  # git echoes the URL on failure

    github_repo = Github(auth=Auth.Token(secret)).get_repo(f"{owner}/{name}")
    pr = github_repo.create_pull(
        title=report.title, body=body, head=branch, base=github_repo.default_branch, draft=True
    )
    return pr.html_url
