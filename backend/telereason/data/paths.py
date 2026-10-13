"""On-disk layout of the downloaded dataset, shared by the downloader and the loader.

``DATA_DIR/factorybench`` is a local copy of the Hugging Face dataset repo, so the
FactoryWave files sit under its ``factorywave/`` folder.  ``infra/setup_droplet.sh``
downloads into the same place (``/root/data/factorybench`` with ``DATA_DIR=/root/data``).
"""

from pathlib import Path

DATASET_DIRNAME = "factorybench"


def dataset_dir(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / DATASET_DIRNAME


def factorywave_dir(data_dir: str | Path) -> Path:
    return dataset_dir(data_dir) / "factorywave"
