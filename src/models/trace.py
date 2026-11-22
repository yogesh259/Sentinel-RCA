from typing import Literal

from pydantic import BaseModel


class Frame(BaseModel):
    raw: str
    language: Literal["java", "python"]
    file: str
    line: int | None
    function: str | None
    is_user_code: bool = False


class ParsedTrace(BaseModel):
    language: Literal["java", "python"]
    exception_type: str
    message: str
    frames: list[Frame]
    caused_by: list[str] = []
