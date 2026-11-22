from src.agents.context import describe_trace
from src.llm.client import structured
from src.models.plan import Plan
from src.models.state import SentinelState
from src.models.trace import ParsedTrace


def orchestrator_node(state: SentinelState) -> dict:
    """Plan the investigation: which frames to focus on and which hypotheses to test."""
    trace = state["trace"]
    plan = structured("orchestrator", Plan, trace=describe_trace(trace))
    return {"plan": plan.model_copy(update={"focus_frames": valid_focus(trace, plan.focus_frames)})}


def valid_focus(trace: ParsedTrace, picked: list[int]) -> list[int]:
    """Keep only real user-code frame indexes; fall back to the top three if the model picked none."""
    user_frames = [i for i, frame in enumerate(trace.frames) if frame.is_user_code]
    chosen = [i for i in dict.fromkeys(picked) if i in user_frames][:3]
    return chosen or user_frames[:3]
