from pathlib import Path

from telereason.data import download


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
            "revision": "main",
            "local_dir": tmp_path.resolve() / "factorybench",
            "allow_patterns": [
                "factorywave/episodes.parquet",
                "factorywave/flow.parquet",
                "factorywave/*_signals.parquet",
                "knowledge_graph/knowledge_graph.json",
            ],
            "token": "test-token",
        }
    ]
