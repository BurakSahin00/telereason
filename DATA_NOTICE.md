# Data notice

This repository contains no datasets and no model weights. They are downloaded at setup time and stay outside the repository.

`fixtures/` contains small example responses derived from FactoryBench (window ids, measured facts and short notes; no raw telemetry). The MIT license of this repository does not cover them; they keep the license of their source data, listed below.

## Datasets

- **FactoryBench**: *FactoryBench: Evaluating Industrial Machine Understanding* (arXiv 2605.07675). Hugging Face `FactoryBench/FactoryBench`, revision `2561c2c90560ae3df90ef8136f774faa5fb4b4e3`. We use the files listed in `backend/telereason/data/download.py` (`DEFAULT_PATTERNS`), unchanged, downloaded at setup time.
  - The dataset card at that revision (last edited 10 Sep 2026) states CC BY 4.0.
  - The anonymised snapshot of the authors' code repository (source commit of 28 Jul 2026) and its Croissant file state CC BY-NC 4.0 for the data and benchmark artefacts.
  - We have asked the authors which statement applies, and whether a hackathon entry that may win a prize is an acceptable use under CC BY-NC 4.0: [FactoryBench discussion #2](https://huggingface.co/datasets/FactoryBench/FactoryBench/discussions/2). Until they answer, we rely on the dataset card at the pinned revision, give attribution, and do not redistribute the raw data. We use none of the authors' code.
- **AURSAD**: Błażej Leporowski, Daniella Tola, Casper Hansen, Alexandros Iosifidis, *AURSAD: Universal Robot Screwdriving Anomaly Detection Dataset*, version 1.0, Zenodo, 2021, DOI [10.5281/zenodo.4487073](https://doi.org/10.5281/zenodo.4487073), CC BY 4.0. We use AURSAD only as it is incorporated in FactoryBench (the items whose provenance names AURSAD), not the Zenodo files. It is cited because the FactoryBench card asks users to cite the upstream datasets it incorporates.
- **voraus-AD**: *voraus-AD: A New Dataset for Anomaly Detection in Robot Applications* (arXiv 2311.04153), licensed CC BY-NC-SA 4.0. FactoryBench incorporates it. The night pipeline drops every item whose provenance names voraus-AD before the gate, facts, notes and report (the 16 Oct night had none to drop: `results/run-20261016-1/startup.log`; the 11 Oct night excluded them too), so no voraus-AD data is used in any result or shown in the app. It is cited because the FactoryBench card asks for it.

## Model

- **gpt-oss-120b** (Hugging Face: `openai/gpt-oss-120b`), Apache 2.0. Downloaded at setup time; not stored in this repository.

## License of this repository

The MIT license in `LICENSE` covers the code in this repository. It does not cover the datasets, the fixtures derived from them or the model above, which keep their own licenses.

The license notes above are informational and are not legal advice.
