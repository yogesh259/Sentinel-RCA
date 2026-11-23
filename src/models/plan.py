from pydantic import BaseModel, Field


class Plan(BaseModel):
    focus_frames: list[int] = Field(max_length=3)
    hypotheses: list[str] = Field(min_length=1, max_length=3)
    look_for: list[str]
