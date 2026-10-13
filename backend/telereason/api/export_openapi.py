"""Write the app's OpenAPI document to docs/openapi.json.

Run from backend/: uv run python -m telereason.api.export_openapi
"""

import json
import logging
from pathlib import Path
from typing import Any

from telereason.api.main import create_app
from telereason.settings import DEFAULT_MODEL, Settings

OPENAPI_PATH = Path(__file__).resolve().parents[3] / "docs" / "openapi.json"

logger = logging.getLogger(__name__)


def build_openapi() -> dict[str, Any]:
    # Fixed settings so the document does not depend on the caller's environment.
    return create_app(Settings(engine="fake", model=DEFAULT_MODEL)).openapi()


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    OPENAPI_PATH.write_text(render(build_openapi()), encoding="utf-8", newline="\n")
    logger.info("Wrote %s", OPENAPI_PATH)


if __name__ == "__main__":
    main()
