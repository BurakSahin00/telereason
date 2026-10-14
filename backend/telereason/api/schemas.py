"""Request, response and stream-event models; field names follow docs/api.md."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Line = Literal["FactoryWave", "AURSAD"]
Verdict = Literal["closed", "screening", "open"]
NightStep = Literal["gate", "facts", "scan", "report"]
RunStatus = Literal["queued", "running", "done", "failed"]
ErrorCode = Literal[
    "bad_request",
    "not_found",
    "busy",
    "rate_limited",
    "engine_not_ready",
    "not_implemented",
]


# Errors


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# GET /api/health


class HealthResponse(BaseModel):
    status: Literal["ok"]
    engine: Literal["fake", "vllm"]
    model: str
    ready: bool


# Night timings (shared by /api/system and /api/report/latest)


class Timings(BaseModel):
    gate: float
    facts: float
    scan: float
    report: float
    total: float


# GET /api/system


class GpuInfo(BaseModel):
    name: str
    memory_total_gib: float
    memory_used_gib: float


class Versions(BaseModel):
    rocm: str | None
    vllm: str | None
    xgboost: str | None
    python: str


class LastNight(BaseModel):
    run_id: str
    timings_s: Timings


class SystemResponse(BaseModel):
    gpu: GpuInfo | None
    versions: Versions
    last_night: LastNight | None


# GET /api/gate


class GateLine(BaseModel):
    line: Line
    verdict: Verdict
    label: str
    auroc: float
    auroc_ci: tuple[float, float]
    explanation: str


class GateResponse(BaseModel):
    run_id: str
    lines: list[GateLine]


# GET /api/report/latest


class ReportItem(BaseModel):
    window_id: str
    task: str
    score: float | None
    look_first: str
    note: str
    verified: bool


class ReportLine(BaseModel):
    line: Line
    verdict: Verdict
    label: str
    scanned: int
    flagged: int | None
    threshold: float | None
    items: list[ReportItem]


class ReportResponse(BaseModel):
    run_id: str
    timings_s: Timings
    lines: list[ReportLine]


# GET /api/windows


class WindowSummary(BaseModel):
    window_id: str
    line: Line
    task: str
    score: float | None
    flagged: bool | None
    look_first: str
    verified: bool


class WindowListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[WindowSummary]


# GET /api/windows/{window_id}


class Fact(BaseModel):
    signal: str
    window_mean: float
    normal_mean: float
    difference: float
    z: float
    plain: str


class FeatureContribution(BaseModel):
    feature: str
    contribution: float


class WindowDetail(BaseModel):
    window_id: str
    line: Line
    task: str
    verdict: Verdict
    score: float | None
    flagged: bool | None
    look_first: str
    facts: list[Fact]
    top_features: list[FeatureContribution]
    note: str
    verified: bool


# GET /api/windows/{window_id}/signals


class SignalChannel(BaseModel):
    name: str
    values: list[float]
    normal_low: list[float]
    normal_high: list[float]


class SignalsResponse(BaseModel):
    window_id: str
    t_s: list[float]
    channels: list[SignalChannel]


# POST /api/ask: request and SSE event payloads


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    window_id: str | None = None


class ToolCallEvent(BaseModel):
    turn: int
    tool: str
    args: dict[str, Any]


class ToolResultEvent(BaseModel):
    turn: int
    tool: str
    summary: str
    ms: int | None


class AnswerEvent(BaseModel):
    answer: str
    answer_source: Literal["tool", "model"]
    text: str


class DoneEvent(BaseModel):
    total_ms: int
    tokens: int


# One SSE message: `event` is the SSE event name, `data` its JSON payload.
# These wrappers only document the stream in /openapi.json.


class ToolCallMessage(BaseModel):
    event: Literal["tool_call"]
    data: ToolCallEvent


class ToolResultMessage(BaseModel):
    event: Literal["tool_result"]
    data: ToolResultEvent


class AnswerMessage(BaseModel):
    event: Literal["answer"]
    data: AnswerEvent


class DoneMessage(BaseModel):
    event: Literal["done"]
    data: DoneEvent


class ErrorMessage(BaseModel):
    event: Literal["error"]
    data: ErrorResponse


AskStreamMessage = Annotated[
    ToolCallMessage | ToolResultMessage | AnswerMessage | DoneMessage | ErrorMessage,
    Field(discriminator="event"),
]


# GET /api/examples


class Example(BaseModel):
    lang: Literal["en", "tr"]
    text: str


class ExamplesResponse(BaseModel):
    examples: list[Example]


# POST /api/night-runs and GET /api/night-runs/{run_id}


class NightRunRequest(BaseModel):
    max_windows: int = Field(default=300, ge=1)


class NightRunStarted(BaseModel):
    run_id: str
    status: RunStatus


class StepTimings(BaseModel):
    """Seconds per finished step; `null` while the step has not finished."""

    gate: float | None = None
    facts: float | None = None
    scan: float | None = None
    report: float | None = None


class NightRunProgress(BaseModel):
    run_id: str
    status: RunStatus
    step: NightStep | None
    done: int
    total: int
    timings_s: StepTimings
