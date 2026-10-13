"""Download TeleReason's public dataset dependencies from Hugging Face.

The repository intentionally contains no telemetry.  This module is an
idempotent setup step: files are downloaded into ``DATA_DIR/factorybench`` (see
``telereason.data.paths``) and are never written into the source tree unless the
caller explicitly points DATA_DIR there.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import snapshot_download

from telereason.data.paths import dataset_dir

DEFAULT_DATASET = "FactoryBench/FactoryBench"
DEFAULT_PATTERNS = (
    "factorywave/episodes.parquet",
    "factorywave/flow.parquet",
    "factorywave/*_signals.parquet",
    "knowledge_graph/knowledge_graph.json",
)


def download_dataset(
    data_dir: str | Path,
    *,
    dataset: str = DEFAULT_DATASET,
    token: str | None = None,
    revision: str = "main",
) -> Path:
    """Download the files needed by the data loader and return the dataset folder.

    ``snapshot_download`` handles resumable downloads and its local directory
    metadata makes repeated setup calls cheap.  The dataset is public, so a
    token is optional; ``HF_TOKEN`` is used when supplied by the environment.
    """

    root = dataset_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    auth_token = token or os.environ.get("HF_TOKEN") or None
    snapshot_download(
        repo_id=dataset,
        repo_type="dataset",
        revision=revision,
        local_dir=root,
        allow_patterns=list(DEFAULT_PATTERNS),
        token=auth_token,
    )
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Download TeleReason data from Hugging Face")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("DATA_DIR", "./data"),
        help="Directory for downloaded data (default: DATA_DIR or ./data)",
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("HF_DATASET", DEFAULT_DATASET),
        help=f"Hugging Face dataset id (default: {DEFAULT_DATASET})",
    )
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    root = download_dataset(args.data_dir, dataset=args.dataset, revision=args.revision)
    print(f"Downloaded {args.dataset} into {root}")


if __name__ == "__main__":
    main()
