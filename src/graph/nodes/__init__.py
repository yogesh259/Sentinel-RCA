"""The five RCA pipeline nodes, in execution order."""

from src.graph.nodes.dedup import dedup_node
from src.graph.nodes.execute import execute_node
from src.graph.nodes.gate import authorisation_gate_node
from src.graph.nodes.investigate import investigate_git_node
from src.graph.nodes.parse_trace import parse_trace_node

__all__ = [
    "authorisation_gate_node",
    "dedup_node",
    "execute_node",
    "investigate_git_node",
    "parse_trace_node",
]
