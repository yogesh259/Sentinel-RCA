"""Keeps a local clone of each GitHub repo under .cache/repos so runs don't re-download it."""

import re
import subprocess
from pathlib import Path

from src.config.settings import get_settings

GITHUB_URL = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")


class RepoError(Exception):
    """The repo URL is invalid, the clone failed, or the repo is too large."""


def run_git(repo: Path, *args: str, ok_codes: tuple[int, ...] = (0,), stdin: str | None = None) -> str:
    """Run one git command in `repo` and return stdout; raise RepoError with git's message on failure."""
    result = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True, text=True, timeout=120)
    if result.returncode not in ok_codes:
        raise RepoError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def parse_repo_url(url: str) -> tuple[str, str]:
    """`https://github.com/owner/repo` -> (owner, repo). Only public GitHub HTTPS URLs are accepted."""
    match = GITHUB_URL.match(url.strip())
    if not match:
        raise RepoError(f"Not a GitHub repo URL: {url!r} (expected https://github.com/<owner>/<repo>)")
    return match["owner"], match["repo"]


def prepare(url: str, ref: str | None = None) -> Path:
    """Clone the repo (or update the cached clone) and check out `ref`, or the default branch.

    The clone is partial (`--filter=blob:none`): file contents download only when read, which keeps
    big repos cheap. It never uses credentials, so private repos fail here by design.
    """
    owner, name = parse_repo_url(url)
    settings = get_settings()
    path = settings.cache_dir / f"{owner}__{name}"
    if (path / ".git").exists():
        run_git(path, "fetch", "--quiet", "origin")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        clone_url = f"https://github.com/{owner}/{name}.git"
        run_git(path.parent, "clone", "--quiet", "--filter=blob:none", "--no-checkout", clone_url, path.name)

    run_git(path, "checkout", "--quiet", "--force", "--detach", ref or "origin/HEAD")
    file_count = len(list_files(path))
    if file_count > settings.max_repo_files:
        raise RepoError(f"Repo has {file_count} files; the limit is {settings.max_repo_files}.")
    return path.resolve()


def list_files(repo: Path) -> list[str]:
    """Every tracked file path, relative to the repo root."""
    return run_git(repo, "ls-files").splitlines()


def apply_patch(repo: Path, patch: str, *, check_only: bool) -> None:
    """`git apply` a unified diff. `--recount` tolerates the wrong hunk line counts LLMs often write."""
    mode = "--check" if check_only else "--index"
    run_git(repo, "apply", mode, "--recount", "-", stdin=patch if patch.endswith("\n") else patch + "\n")
