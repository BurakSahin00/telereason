"""Engine smoke test, run by hand on the droplet: `python -m telereason.ai.smoke`.

Opens the engine named by `ENGINE` (vllm on the droplet), sends one short LIVE and one short
NIGHT request together, logs the startup time and, per request, the duration and the first
200 characters of the answer, then closes the engine. Tests never run it.
"""

import asyncio
import logging
import time

from telereason.ai.engine import Engine, Generation, GenerationKind, Priority, create_engine
from telereason.settings import load_settings

logger = logging.getLogger("telereason.ai.smoke")

SYSTEM = (
    "<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n\n"
    "Reasoning: low\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every "
    "message.<|end|>"
)
REQUESTS: tuple[tuple[Priority, GenerationKind, str], ...] = (
    (
        Priority.LIVE,
        "agent",
        "In one sentence, what should a technician check first when a robot joint motor runs hot?",
    ),
    (
        Priority.NIGHT,
        "note",
        "In two sentences, explain why a nightly telemetry scan helps a maintenance team.",
    ),
)
MAX_TOKENS = 256
PREVIEW_CHARS = 200


def harmony_prompt(question: str) -> str:
    return f"{SYSTEM}<|start|>user<|message|>{question}<|end|><|start|>assistant"


async def ask(engine: Engine, priority: Priority, kind: GenerationKind, question: str) -> None:
    result: Generation = await engine.generate(
        harmony_prompt(question), priority=priority, max_tokens=MAX_TOKENS, kind=kind
    )
    logger.info(
        "%s: %d ms, %d prompt + %d completion tokens\n%s",
        priority.name,
        result.elapsed_ms,
        result.prompt_tokens,
        result.completion_tokens,
        result.text[:PREVIEW_CHARS],
    )


async def main() -> None:
    settings = load_settings()
    engine = create_engine(settings)
    if engine.name == "fake":
        logger.warning("ENGINE=fake; set ENGINE=vllm to test the real engine")

    started = time.perf_counter()
    await engine.start()
    logger.info("%s engine ready in %.1f s", engine.name, time.perf_counter() - started)
    try:
        await asyncio.gather(*(ask(engine, *request) for request in REQUESTS))
    finally:
        await engine.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
