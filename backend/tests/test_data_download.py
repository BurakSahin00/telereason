import re
import sys
from pathlib import Path

from telereason.data import download

SETUP_DROPLET = Path(__file__).resolve().parents[2] / "infra" / "setup_droplet.sh"


def test_download_dataset_is_idempotent_setup_boundary(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(tmp_path)

    monkeypatch.setattr(download, "snapshot_download", fake_snapshot_download)

    result = download.download_dataset(tmp_path, token="test-token")

    assert result == tmp_path.resolve() / "factorybench"
    assert calls == [
        {
            "repo_id": "FactoryBench/FactoryBench",
            "repo_type": "dataset",
            "revision": "2561c2c90560ae3df90ef8136f774faa5fb4b4e3",
            "local_dir": tmp_path.resolve() / "factorybench",
            "allow_patterns": [
                "factorybench_qa/level_4.jsonl",
                "factorybench_lite/level_1.jsonl",
                "factorybench_lite/level_2.jsonl",
                "factorybench_lite/level_3.jsonl",
                "factorybench_lite/level_4.jsonl",
                "factorywave/episodes.parquet",
                "factorywave/flow.parquet",
                "knowledge_graph/knowledge_graph.json",
            ],
            "token": "test-token",
        }
    ]


def test_cli_defaults_to_the_pinned_revision(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_download_dataset(data_dir: str, **kwargs: object) -> Path:
        calls.append(kwargs)
        return tmp_path

    monkeypatch.setattr(download, "download_dataset", fake_download_dataset)
    monkeypatch.setattr(sys, "argv", ["download", "--data-dir", str(tmp_path)])
    monkeypatch.delenv("HF_DATASET", raising=False)

    download.main()

    assert calls == [{"dataset": download.DEFAULT_DATASET, "revision": download.DEFAULT_REVISION}]


def test_setup_droplet_downloads_the_same_revision_and_files() -> None:
    script = SETUP_DROPLET.read_text(encoding="utf-8")

    assert re.findall(r'^DATASET_REVISION="([0-9a-f]+)"', script, re.MULTILINE) == [
        download.DEFAULT_REVISION
    ]
    assert '--revision "$DATASET_REVISION"' in script
    assert tuple(re.findall(r'--include "([^"]+)"', script)) == download.DEFAULT_PATTERNS
