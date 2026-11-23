"""State shared by every LLM call of one graph run: token usage and the visitor's API keys.

Both live in ContextVars. LangGraph copies the context into each node's thread, so the parallel
agents of one run share them and concurrent runs never mix.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class Usage:
    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_llm_call(self, model: str, tokens: int, cost_usd: float) -> None:
        with self.lock:
            self.llm_calls += 1
            self.tokens += tokens
            self.cost_usd += cost_usd
            self.models.add(model)

    def record_tool_call(self) -> None:
        with self.lock:
            self.tool_calls += 1


current_usage: ContextVar[Usage | None] = ContextVar("current_usage", default=None)
visitor_keys: ContextVar[dict[str, str] | None] = ContextVar("visitor_keys", default=None)


@contextmanager
def track_usage() -> Iterator[Usage]:
    """Collect the tokens and cost of one graph run (or resume)."""
    usage = Usage()
    token = current_usage.set(usage)
    try:
        yield usage
    finally:
        current_usage.reset(token)


@contextmanager
def use_visitor_keys(keys: dict[str, str]) -> Iterator[None]:
    """Use the visitor's API keys instead of the server's for one run. They never reach state, logs or disk."""
    token = visitor_keys.set({provider: key.strip() for provider, key in keys.items() if key and key.strip()})
    try:
        yield
    finally:
        visitor_keys.reset(token)
