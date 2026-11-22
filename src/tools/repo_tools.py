"""Read-only code tools for the locator agent. Each tool takes one Pydantic args model."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.services.repo_cache import run_git

SKIPPED_DIRS = {".git", "build", "target", "node_modules", "__pycache__"}
MAX_READ_LINES = 200
MAX_SEARCH_HITS = 30
MAX_DIR_ENTRIES = 100


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    path: str = Field(description="File path relative to the repo root")
    start: int = Field(1, ge=1, description="First line to read")
    end: int = Field(120, ge=1, description="Last line to read")


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    pattern: str = Field(description="Regular expression to search for")
    glob: str = Field("*", description="Limit the search to paths matching this glob, e.g. '*.java'")


class ListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled argument is an error the LLM sees

    path: str = Field("", description="Directory relative to the repo root; empty for the root")


class RepoTools:
    """Tools bound to one checked-out repo. Paths can never leave the repo root."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()

    def inside_repo(self, path: str) -> Path:
        """Resolve a path the LLM gave us and refuse anything outside the repo (../, absolute paths)."""
        resolved = (self.repo / path).resolve()
        if not resolved.is_relative_to(self.repo):
            raise ValueError(f"{path!r} is outside the repository")
        return resolved

    def read_file(self, args: ReadFileArgs) -> str:
        """Read a range of lines from a file (max 200 per call). Lines come back numbered."""
        lines = self.inside_repo(args.path).read_text(encoding="utf-8", errors="replace").splitlines()
        end = min(args.end, args.start + MAX_READ_LINES - 1, len(lines))
        numbered = [f"{n:>5} | {lines[n - 1]}" for n in range(args.start, end + 1)]
        return f"{args.path} ({len(lines)} lines total)\n" + "\n".join(numbered)

    def search_code(self, args: SearchCodeArgs) -> str:
        """Search tracked files with `git grep` (regex). Returns up to 30 `path:line:text` hits."""
        # git grep exits 1 when nothing matches; that is an answer, not an error.
        output = run_git(self.repo, "grep", "-n", "-I", "-E", "-e", args.pattern, "--", args.glob, ok_codes=(0, 1))
        hits = output.splitlines()
        if not hits:
            return "no matches"
        more = f"\n... {len(hits) - MAX_SEARCH_HITS} more hits" if len(hits) > MAX_SEARCH_HITS else ""
        return "\n".join(hits[:MAX_SEARCH_HITS]) + more

    def list_dir(self, args: ListDirArgs) -> str:
        """List a directory (max 100 entries). Directories end with '/'."""
        directory = self.inside_repo(args.path)
        entries = sorted(
            f"{entry.name}/" if entry.is_dir() else entry.name
            for entry in directory.iterdir()
            if entry.name not in SKIPPED_DIRS
        )
        return "\n".join(entries[:MAX_DIR_ENTRIES]) or "(empty)"
