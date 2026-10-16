"""GET /api/system: the GPU, software versions and last night's timings for the AMD panel.

Fake mode serves `fixtures/system.json`. Real mode asks `rocm-smi` for the GPU on every
request (memory in use changes while the engine runs), reads the versions once at startup
(`create_app` stores them), and reports the timings of the run that finished last with
NIGHT_SOURCE=db, or the stored night's timings with NIGHT_SOURCE=stored.
"""

import json
import logging
import platform
import re
import shutil
import subprocess
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Request

from telereason.api.fake import (
    db_night,
    fixture_path,
    fixture_response,
    is_fake,
    serves_stored_night,
)
from telereason.api.schemas import GpuInfo, LastNight, SystemResponse, Versions

ROCM_SMI_ARGS = ("--showproductname", "--showmeminfo", "vram", "--json")
ROCM_SMI_TIMEOUT_S = 2.0
ROCM_VERSION_FILE = "/opt/rocm/.info/version"
BYTES_PER_GIB = 2**30
# Keys are compared lower-case: rocm-smi releases differ in spelling ("Card Series",
# "Card series") and in which product name they print. Best name first.
NAME_KEYS = ("card series", "device name", "marketing name", "card model")
MODEL_KEY = "card model"
VENDOR_KEY = "card vendor"
GFX_KEY = "gfx version"
# Product names of AMD PCI device ids, used when rocm-smi prints no readable name. A device id
# means something only for its vendor, so the table is read only for an AMD card. 0x74b5 is
# the MI300X virtual function: it matches what amd-smi and rocm-smi read from the same card
# (subsystem 0x74a1, gfx942, 304 compute units, 191.7 GiB VRAM) on the AMD Developer Cloud
# MI300X droplet on 17 Oct.
KNOWN_DEVICES = {
    "0x74a1": "AMD Instinct MI300X",
    "0x74b5": "AMD Instinct MI300X VF",
}
MEMORY_TOTAL_KEY = "vram total memory (b)"
MEMORY_USED_KEY = "vram total used memory (b)"
CARD_KEY = re.compile(r"card(\d+)", re.IGNORECASE)
STORED_SYSTEM = "system.json"

logger = logging.getLogger(__name__)
router = APIRouter()


def read_gpu() -> GpuInfo | None:
    """The GPU as `rocm-smi` reports it, or None when rocm-smi is missing, fails, takes
    longer than ROCM_SMI_TIMEOUT_S or prints no card we can read. Never raises."""
    executable = shutil.which("rocm-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *ROCM_SMI_ARGS],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=ROCM_SMI_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("rocm-smi did not answer: %s", exc)
        return None
    # A non-zero exit can still come with a full card (rocm-smi exits non-zero when some
    # other field is not supported), so the output decides.
    gpu = parse_rocm_smi(result.stdout)
    if gpu is None:
        logger.warning(
            "no GPU in rocm-smi output (exit %d): %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
    return gpu


def parse_rocm_smi(output: str) -> GpuInfo | None:
    """The lowest-numbered card in `rocm-smi --json` output that has a name and VRAM.

    Warning lines before the JSON are skipped. Returns None for anything else."""
    start = output.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(output, start)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    cards = sorted(
        (int(match.group(1)), value)
        for key, value in data.items()
        if (match := CARD_KEY.fullmatch(str(key).strip())) and isinstance(value, dict)
    )
    for _, fields in cards:
        gpu = _card(fields)
        if gpu is not None:
            return gpu
    return None


def _card(fields: dict[Any, Any]) -> GpuInfo | None:
    by_key = {str(key).strip().lower(): value for key, value in fields.items()}
    names = (_name(by_key.get(key)) for key in NAME_KEYS)
    name = next((candidate for candidate in names if candidate), None) or _fallback_name(by_key)
    total = _bytes(by_key.get(MEMORY_TOTAL_KEY))
    used = _bytes(by_key.get(MEMORY_USED_KEY))
    if name is None or not total or used is None:
        return None
    return GpuInfo(
        name=name,
        memory_total_gib=total / BYTES_PER_GIB,
        memory_used_gib=used / BYTES_PER_GIB,
    )


def _fallback_name(by_key: dict[str, Any]) -> str | None:
    """The name of a known AMD device id, else "AMD <gfx version>", else None."""
    vendor = by_key.get(VENDOR_KEY)
    model = by_key.get(MODEL_KEY)
    if isinstance(vendor, str) and _is_amd(vendor) and isinstance(model, str):
        if known := KNOWN_DEVICES.get(model.strip().lower()):
            return known
    gfx = by_key.get(GFX_KEY)
    if isinstance(gfx, str) and re.fullmatch(r"gfx[0-9a-f]+", gfx.strip(), re.IGNORECASE):
        return f"AMD {gfx.strip().lower()}"
    return None


def _is_amd(vendor: str) -> bool:
    return "advanced micro devices" in vendor.lower() or "amd" in re.split(r"\W+", vendor.lower())


def _name(value: object) -> str | None:
    """A product name. "N/A" (unsupported field) and hex ids such as "0x74a1" (the card
    model on some releases) are not."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.upper() == "N/A" or re.fullmatch(r"0x[0-9a-f]+", name, re.IGNORECASE):
        return None
    return name


def _bytes(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        count = int(value)
    except ValueError:
        return None
    return count if count >= 0 else None


def read_versions() -> Versions:
    """ROCm, vLLM and XGBoost versions (None when missing) and Python as "3.12"."""
    return Versions(
        rocm=_rocm_version(),
        vllm=_package_version("vllm"),
        # Our ROCm build is installed under its own distribution name.
        xgboost=_package_version("xgboost", "amd_xgboost"),
        python=".".join(platform.python_version_tuple()[:2]),
    )


def _rocm_version() -> str | None:
    try:
        with open(ROCM_VERSION_FILE, encoding="utf-8", errors="replace") as file:
            version = file.readline().strip()
    except OSError:
        return None
    return version or None


def _package_version(*names: str) -> str | None:
    """The version of the first of `names` that is installed."""
    for name in names:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return None


def stored_last_night(request: Request) -> LastNight | None:
    """Timings of the stored 11 Oct night, the same as in `report_latest.json`."""
    stored = fixture_path(request, STORED_SYSTEM).read_text(encoding="utf-8")
    return SystemResponse.model_validate_json(stored).last_night


# A plain `def`: FastAPI runs it in a worker thread, so waiting on rocm-smi never blocks the
# event loop the engine shares.
@router.get("/api/system", response_model=SystemResponse)
def system(request: Request) -> SystemResponse:
    if is_fake(request):
        return fixture_response(request, SystemResponse, STORED_SYSTEM, "GET /api/system")
    versions: Versions = request.app.state.versions
    if (night := db_night(request)) is not None:
        last_night = night.last_night()
    else:
        last_night = stored_last_night(request) if serves_stored_night(request) else None
    return SystemResponse(gpu=read_gpu(), versions=versions, last_night=last_night)
