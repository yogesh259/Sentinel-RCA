"""Deterministic evidence retrieval for root-cause analysis.

Code (not the LLM) decides *which* commits are worth looking at, using signals a human
SRE would use: who last changed the failing lines, who changed the code around them,
and what recently touched the file. The LLM then only judges a short, ranked list.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from src.schemas.mcp.git import BlameRange, CommitInfo, DiffOutput
from src.services.mcp_client import call_tool

logger = logging.getLogger("sentinel-rca.rca_evidence")

MAX_CANDIDATES = 3
MAX_FRAMES = 2
BLAME_WINDOW = 5
FILE_WINDOW = 20
DIFF_CHAR_BUDGET = 3000
RECENT_COMMITS = 5

# Signal weights: the commit that last changed the exact failing line is the strongest hint.
EXACT_LINE_WEIGHT = 3.0
NEARBY_LINES_WEIGHT = 2.0
RECENT_FILE_WEIGHT = 1.0
# The deepest frame matters most; its caller gets half the weight.
FRAME_DECAY = 0.5


@dataclass
class Candidate:
    """A commit that might have introduced the failure, with the reasons it was picked."""

    sha: str
    message: str
    author: str
    date: str
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    diff: str = ""

    def add_signal(self, weight: float, reason: str) -> None:
        """Accumulate evidence; one commit can be hit by several signals and frames."""
        self.score += weight
        if reason not in self.signals:
            self.signals.append(reason)

    def to_state(self) -> dict:
        """Compact form stored on graph state and shown to reviewers."""
        return {
            "sha": self.sha,
            "short_sha": self.sha[:7],
            "message": self.message,
            "author": self.author,
            "date": self.date,
            "score": round(self.score, 2),
            "signals": self.signals,
        }


@dataclass
class Evidence:
    """Everything the judge model sees for one incident."""

    candidates: list[Candidate]
    file_path: str = ""
    line: int = 0
    file_excerpt: str = ""


def _candidate(pool: dict[str, Candidate], sha: str, message: str, author: str, date: str) -> Candidate:
    """Get-or-create a candidate so signals for the same commit merge."""
    if sha not in pool:
        pool[sha] = Candidate(sha=sha, message=message, author=author, date=date)
    return pool[sha]


async def _score_frame(pool: dict[str, Candidate], frame: dict, weight: float) -> None:
    """Add blame and recent-history signals for one repository frame."""
    path, line = frame["path"], frame.get("line", 0)
    blame_args = {"file_path": path, "start_line": max(1, line - BLAME_WINDOW), "end_line": line + BLAME_WINDOW}
    blame_task = (
        call_tool("git", "get_blame_range", blame_args, list[BlameRange])
        if line
        else asyncio.sleep(0, result=[])
    )
    recent_args = {"file_path": path, "limit": RECENT_COMMITS}
    blame, recent = await asyncio.gather(
        blame_task,
        call_tool("git", "get_recent_commits", recent_args, list[CommitInfo]),
        return_exceptions=True,
    )

    if isinstance(blame, Exception):
        logger.warning("Blame failed for %s:%s: %s", path, line, blame)
    else:
        for block in blame:
            exact = block.start_line <= line <= block.end_line
            cand = _candidate(pool, block.commit_sha, block.message, block.author, block.date)
            if exact:
                cand.add_signal(EXACT_LINE_WEIGHT * weight, f"last changed {path}:{line}")
            else:
                cand.add_signal(
                    NEARBY_LINES_WEIGHT * weight,
                    f"changed {path}:{block.start_line}-{block.end_line} near the failing line",
                )

    if isinstance(recent, Exception):
        logger.warning("Recent commits failed for %s: %s", path, recent)
    else:
        for commit in recent:
            cand = _candidate(pool, commit.sha, commit.message, commit.author, commit.date)
            cand.add_signal(RECENT_FILE_WEIGHT * weight, f"recently modified {path}")


def diff_for_file(diff: str, path: str, budget: int = DIFF_CHAR_BUDGET) -> str:
    """Keep only the diff section for *path*: other files in the commit are noise."""
    sections = diff.split("diff --git ")
    relevant = [s for s in sections if path and path in s.split("\n", 1)[0]]
    text = "diff --git " + relevant[0] if relevant else diff
    return text[:budget]


def numbered_window(content: str, line: int, radius: int = FILE_WINDOW) -> str:
    """Return the lines around *line*, numbered, with the failing line marked."""
    lines = content.splitlines()
    if not line or line > len(lines):
        return "\n".join(f"{i + 1:>4}  {text}" for i, text in enumerate(lines[: radius * 2]))
    start, end = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(
        f"{'>>' if n == line else '  '}{n:>4}  {lines[n - 1]}" for n in range(start, end + 1)
    )


async def gather_evidence(frames: list[dict]) -> Evidence:
    """Rank candidate commits for the deepest repository frames and fetch their diffs."""
    repo_frames = [f for f in frames if f.get("path")][:MAX_FRAMES]
    if not repo_frames:
        return Evidence(candidates=[])

    pool: dict[str, Candidate] = {}
    await asyncio.gather(
        *(_score_frame(pool, f, FRAME_DECAY**i) for i, f in enumerate(repo_frames))
    )
    ranked = sorted(pool.values(), key=lambda c: (c.score, c.date), reverse=True)[:MAX_CANDIDATES]

    primary = repo_frames[0]
    diffs, content = await asyncio.gather(
        asyncio.gather(
            *(call_tool("git", "get_commit_diff", {"commit_sha": c.sha}, DiffOutput) for c in ranked),
            return_exceptions=True,
        ),
        call_tool("git", "get_file_contents", {"file_path": primary["path"]}, str),
        return_exceptions=True,
    )
    for cand, diff in zip(ranked, diffs if isinstance(diffs, list) else [], strict=False):
        cand.diff = "" if isinstance(diff, Exception) else diff_for_file(diff.diff, primary["path"])

    excerpt = "" if isinstance(content, Exception) else numbered_window(content, primary.get("line", 0))
    return Evidence(
        candidates=ranked,
        file_path=primary["path"],
        line=primary.get("line", 0),
        file_excerpt=excerpt,
    )
