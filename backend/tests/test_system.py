"""GET /api/system in real mode: rocm-smi, installed versions and the stored night's timings.

rocm-smi output is hand-written in the shape rocm-smi --json prints; the droplet's exact
output is not recorded yet, so the parser is tested against several spellings.
"""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telereason.ai.engine import FakeEngine
from telereason.api import system
from telereason.api.main import create_app
from telereason.api.schemas import GpuInfo, Versions
from telereason.settings import DEFAULT_MODEL, Settings
from tests.test_fixtures import load

ROCM_SMI_PATH = "/opt/rocm/bin/rocm-smi"
TOTAL_B = 205822885888
USED_B = 171926372352
EXPECTED_GPU = GpuInfo(
    name="AMD Instinct MI300X",
    memory_total_gib=191.6875,
    memory_used_gib=USED_B / 2**30,
)
CARD = {
    "Card Series": "AMD Instinct MI300X",
    "Card Model": "0x74a1",
    "Card Vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
    "Card SKU": "M3000100",
    "GFX Version": "gfx942",
    "VRAM Total Memory (B)": str(TOTAL_B),
    "VRAM Total Used Memory (B)": str(USED_B),
}
ROCM_SMI_OUTPUT = json.dumps({"card0": CARD})
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
INSTALLED = {"vllm": "0.27.1", "xgboost": "3.2.0"}


@dataclass
class FakeRun:
    """Stands in for subprocess.run and records how it was called."""

    stdout: str = ROCM_SMI_OUTPUT
    returncode: int = 0
    stderr: str = ""
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"args": args, **kwargs})
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def rocm_smi(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    run = FakeRun()
    monkeypatch.setattr(system.shutil, "which", lambda name: ROCM_SMI_PATH)
    monkeypatch.setattr(system.subprocess, "run", run)
    return run


@pytest.fixture
def no_rocm_smi(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    run = FakeRun(error=AssertionError("rocm-smi must not run"))
    monkeypatch.setattr(system.shutil, "which", lambda name: None)
    monkeypatch.setattr(system.subprocess, "run", run)
    return run


def set_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rocm: str | None, packages: dict[str, str]
) -> list[str]:
    """Point the ROCm version file and package metadata at test values; returns the
    package names looked up."""
    version_file = tmp_path / "rocm-version"
    if rocm is not None:
        version_file.write_text(rocm, encoding="utf-8")
    monkeypatch.setattr(system, "ROCM_VERSION_FILE", str(version_file))
    looked_up: list[str] = []

    def version(name: str) -> str:
        looked_up.append(name)
        if name not in packages:
            raise metadata.PackageNotFoundError(name)
        return packages[name]

    monkeypatch.setattr(system.metadata, "version", version)
    return looked_up


def real_app(tmp_path: Path) -> FastAPI:
    settings = Settings(engine="vllm", model=DEFAULT_MODEL, data_dir=str(tmp_path / "data"))
    return create_app(settings, engine=FakeEngine(model=DEFAULT_MODEL))


# rocm-smi output parsing


@pytest.mark.parametrize(
    "output",
    [
        ROCM_SMI_OUTPUT,
        "WARNING: AMD GPU device(s) is/are in a low-power state.\n\n" + ROCM_SMI_OUTPUT + "\n",
        json.dumps(
            {
                "card0": {
                    "Card series": "AMD Instinct MI300X",
                    "Card model": "0x74a1",
                    "VRAM Total Memory (B)": TOTAL_B,
                    "VRAM Total Used Memory (B)": USED_B,
                },
                "system": {"Driver version": "6.8.5"},
            }
        ),
        json.dumps({"card0": {**CARD, "Card Series": "N/A", "Device Name": "AMD Instinct MI300X"}}),
        json.dumps({"card1": {**CARD, "Card Series": "Other GPU"}, "card0": CARD}),
        json.dumps({"card0": {"Card Model": "0x74a1"}, "card1": CARD}),
    ],
    ids=[
        "rocm-smi json",
        "warning before json",
        "older spelling and numbers",
        "device name only",
        "lowest card first",
        "first card incomplete",
    ],
)
def test_parse_reads_card(output: str) -> None:
    assert system.parse_rocm_smi(output) == EXPECTED_GPU


@pytest.mark.parametrize(
    "output",
    [
        "",
        "ERROR: GPU[0] : Unable to get product name\n",
        '{"card0": {"Card Series": ',
        "[1, 2]",
        '{"card0": "AMD Instinct MI300X"}',
        '{"system": {"Driver version": "6.8.5"}}',
        json.dumps({"card0": {"Card Series": "AMD Instinct MI300X"}}),
        json.dumps(
            {
                "card0": {
                    **CARD,
                    "VRAM Total Memory (B)": "N/A",
                    "VRAM Total Used Memory (B)": "N/A",
                }
            }
        ),
        json.dumps(
            {
                "card0": {
                    "Card Model": "0x74a1",
                    "VRAM Total Memory (B)": str(TOTAL_B),
                    "VRAM Total Used Memory (B)": str(USED_B),
                }
            }
        ),
        json.dumps({"card0": {**CARD, "VRAM Total Memory (B)": "0"}}),
        json.dumps({"card0": {**CARD, "VRAM Total Used Memory (B)": "-1"}}),
    ],
    ids=[
        "empty",
        "no json",
        "cut off",
        "not an object",
        "card not an object",
        "no card",
        "no memory",
        "memory not a number",
        "hex id only",
        "zero total",
        "negative used",
    ],
)
def test_parse_rejects_unreadable_output(output: str) -> None:
    assert system.parse_rocm_smi(output) is None


# Running rocm-smi


def test_read_gpu_runs_rocm_smi_with_timeout(rocm_smi: FakeRun) -> None:
    assert system.read_gpu() == EXPECTED_GPU

    (call,) = rocm_smi.calls
    assert call["args"][0] == ROCM_SMI_PATH
    assert {"--showproductname", "--showmeminfo", "vram", "--json"} <= set(call["args"])
    assert call["timeout"] == 2


def test_read_gpu_without_rocm_smi(no_rocm_smi: FakeRun) -> None:
    assert system.read_gpu() is None
    assert no_rocm_smi.calls == []


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(ROCM_SMI_PATH, 2),
        PermissionError(13, "Permission denied"),
        FileNotFoundError(2, "No such file or directory"),
    ],
    ids=["timeout", "permission denied", "removed after which"],
)
def test_read_gpu_when_rocm_smi_fails_to_run(rocm_smi: FakeRun, error: Exception) -> None:
    rocm_smi.error = error

    assert system.read_gpu() is None


def test_read_gpu_when_rocm_smi_exits_with_error(rocm_smi: FakeRun) -> None:
    rocm_smi.returncode = 1
    rocm_smi.stdout = ""
    rocm_smi.stderr = "ERROR: No AMD GPUs specified\n"

    assert system.read_gpu() is None


def test_read_gpu_keeps_full_card_on_non_zero_exit(rocm_smi: FakeRun) -> None:
    # rocm-smi exits non-zero when some field is not supported, even with a full card.
    rocm_smi.returncode = 2

    assert system.read_gpu() == EXPECTED_GPU


# Versions


@pytest.mark.parametrize(
    ("content", "expected"),
    [("7.2.3\n", "7.2.3"), ("  7.2.3-70  \nmore\n", "7.2.3-70"), ("", None), ("\n", None)],
    ids=["version", "first line stripped", "empty", "blank"],
)
def test_rocm_version_from_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str, expected: str | None
) -> None:
    set_versions(monkeypatch, tmp_path, content, INSTALLED)

    assert system.read_versions().rocm == expected


def test_versions_when_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_versions(monkeypatch, tmp_path, "7.2.3\n", INSTALLED)

    assert system.read_versions() == Versions(
        rocm="7.2.3", vllm="0.27.1", xgboost="3.2.0", python=PYTHON
    )


def test_versions_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_versions(monkeypatch, tmp_path, None, {})

    assert system.read_versions() == Versions(rocm=None, vllm=None, xgboost=None, python=PYTHON)


# The endpoint in real mode


def test_real_mode_reports_gpu_versions_and_stored_night(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rocm_smi: FakeRun
) -> None:
    set_versions(monkeypatch, tmp_path, "7.2.3\n", INSTALLED)
    client = TestClient(real_app(tmp_path))

    response = client.get("/api/system")

    assert response.status_code == 200
    assert response.json() == {
        "gpu": EXPECTED_GPU.model_dump(),
        "versions": {"rocm": "7.2.3", "vllm": "0.27.1", "xgboost": "3.2.0", "python": PYTHON},
        "last_night": load("system.json")["last_night"],
    }


@pytest.mark.parametrize(
    ("found", "run"),
    [
        (None, FakeRun()),
        (ROCM_SMI_PATH, FakeRun(error=subprocess.TimeoutExpired(ROCM_SMI_PATH, 2))),
        (ROCM_SMI_PATH, FakeRun(error=OSError("exec format error"))),
        (ROCM_SMI_PATH, FakeRun(stdout='{"card0": {"Card Series": ')),
    ],
    ids=["missing", "timeout", "os error", "broken output"],
)
def test_real_mode_answers_without_gpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, found: str | None, run: FakeRun
) -> None:
    monkeypatch.setattr(system.shutil, "which", lambda name: found)
    monkeypatch.setattr(system.subprocess, "run", run)
    client = TestClient(real_app(tmp_path))

    response = client.get("/api/system")

    assert response.status_code == 200
    assert response.json()["gpu"] is None
    assert response.json()["last_night"] == load("system.json")["last_night"]


def test_versions_are_read_once_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_rocm_smi: FakeRun
) -> None:
    looked_up = set_versions(monkeypatch, tmp_path, "7.2.3\n", INSTALLED)
    client = TestClient(real_app(tmp_path))
    assert sorted(looked_up) == ["vllm", "xgboost"]

    (tmp_path / "rocm-version").write_text("9.9.9\n", encoding="utf-8")
    first = client.get("/api/system").json()
    second = client.get("/api/system").json()

    assert sorted(looked_up) == ["vllm", "xgboost"]
    assert first["versions"] == second["versions"]
    assert first["versions"]["rocm"] == "7.2.3"


def test_stored_last_night_matches_report() -> None:
    report = load("report_latest.json")

    assert load("system.json")["last_night"] == {
        "run_id": report["run_id"],
        "timings_s": report["timings_s"],
    }


def test_fake_mode_serves_fixture_without_rocm_smi(no_rocm_smi: FakeRun) -> None:
    client = TestClient(create_app(Settings(engine="fake", model=DEFAULT_MODEL)))

    response = client.get("/api/system")

    assert response.status_code == 200
    assert response.json() == load("system.json")
    assert no_rocm_smi.calls == []
