"""Response models; field names follow docs/api.md."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    engine: Literal["fake", "vllm"]
    model: str
    ready: bool
