from pydantic import BaseModel, ConfigDict


class ToolArgs(BaseModel):
    """Base for tool inputs. Unknown fields are rejected, so the model hears about a misspelled argument."""

    model_config = ConfigDict(extra="forbid")
