"""Git access for one run: a fresh clone per run, plus the git helpers the tools and nodes share."""

import difflib
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from src.config.settings import get_settings
from src.models.report import CodeEdit

CLONE_PREFIX = "sentinel-"
CLONE_MAX_AGE_S = 2 * 60 * 60
LINE_HISTORY_COMMITS = 4
LINE_HISTORY_CHARS = 2500
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


def clone(url: str, ref: str | None = None) -> Path:
    """Clone the repo into a fresh temporary folder for this run and check out `ref` (or the default branch).

    Each run gets its own clone, so concurrent runs on the same repo never change each other's files.
    The clone is partial (`--filter=blob:none`): file contents download only when read, which keeps big
    repos cheap. It never uses credentials, so private repos fail here by design.
    """
    owner, name = parse_repo_url(url)
    remove_stale_clones()
    path = Path(tempfile.mkdtemp(prefix=f"{CLONE_PREFIX}{owner}__{name}-"))
    try:
        clone_url = f"https://github.com/{owner}/{name}.git"
        run_git(path, "clone", "--quiet", "--filter=blob:none", "--no-checkout", clone_url, ".")
        commit = resolve_commit(path, ref or "origin/HEAD")
        run_git(path, "checkout", "--quiet", "--detach", commit)
        limit = get_settings().max_repo_files
        if (file_count := len(list_files(path))) > limit:
            raise RepoError(f"Repo has {file_count} files; the limit is {limit}.")
    except RepoError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return path.resolve()


def resolve_commit(repo: Path, ref: str) -> str:
    """The full SHA for a branch, tag or SHA; a clear error instead of git's (and no option injection)."""
    try:
        return run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}").strip()
    except RepoError:
        raise RepoError(f"Unknown commit or branch: {ref!r}") from None


def remove_stale_clones() -> None:
    """Delete clones older than CLONE_MAX_AGE. A run paused for approval keeps its clone until then,
    because create_pr applies the patch in it; anything older was abandoned."""
    cutoff = time.time() - CLONE_MAX_AGE_S
    for old in Path(tempfile.gettempdir()).glob(f"{CLONE_PREFIX}*"):
        if old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)


def list_files(repo: Path) -> list[str]:
    """Every tracked file path, relative to the repo root."""
    return run_git(repo, "ls-files").splitlines()


def line_history(repo: Path, file: str, start: int, end: int) -> str:
    """How lines start-end of a file changed, newest commit first (`git log -L`), or "" if git can't tell."""
    try:
        output = run_git(
            repo,
            "log",
            f"-n{LINE_HISTORY_COMMITS}",
            "--date=short",
            "--format=commit %h %ad %s",
            f"-L{start},{end}:{file}",
        )
    except RepoError:
        return ""
    headers = ("diff --git", "index ", "--- ", "+++ ")
    return "\n".join(line for line in output.splitlines() if not line.startswith(headers))[:LINE_HISTORY_CHARS]


def build_patch(repo: Path, edits: list[CodeEdit]) -> str | None:
    """A unified diff from find/replace edits, or None if any edit's lines are not in the file."""
    diffs = []
    for change in edits:
        path = (repo / change.file).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file():
            return None
        old = path.read_text(encoding="utf-8").splitlines(keepends=True)
        new = replace_lines(old, change.find, change.replace)
        if new is None:
            return None
        header = f"diff --git a/{change.file} b/{change.file}\n"  # marks where one file's hunks end
        diffs.append(header + "".join(difflib.unified_diff(old, new, f"a/{change.file}", f"b/{change.file}")))
    return "".join(diffs) or None


def replace_lines(lines: list[str], find: str, replace: str) -> list[str] | None:
    """Swap the block matching `find` (ignoring indentation) for `replace`, re-indented to fit."""
    wanted = [line.strip() for line in find.strip("\n").splitlines()]
    for start in range(len(lines) - len(wanted) + 1):
        if [line.strip() for line in lines[start : start + len(wanted)]] == wanted:
            indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
            body = textwrap.dedent(replace.strip("\n")).splitlines()
            new = [f"{indent}{line}\n" if line.strip() else "\n" for line in body]
            return lines[:start] + new + lines[start + len(wanted) :]
    return None


def apply_patch(repo: Path, patch: str, *, check_only: bool) -> None:
    """`git apply` a unified diff. `--recount` tolerates the wrong hunk line counts LLMs often write."""
    mode = "--check" if check_only else "--index"
    run_git(repo, "apply", mode, "--recount", "-", stdin=patch if patch.endswith("\n") else patch + "\n")
