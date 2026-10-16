from fastapi import APIRouter, Request

from telereason.api.fake import fixture_path
from telereason.api.schemas import ExamplesResponse

router = APIRouter()


@router.get("/api/examples", response_model=ExamplesResponse)
def examples(request: Request) -> ExamplesResponse:
    # Fixed example questions from docs/api.md, the same in every mode and night source.
    text = fixture_path(request, "examples.json").read_text(encoding="utf-8")
    return ExamplesResponse.model_validate_json(text)
