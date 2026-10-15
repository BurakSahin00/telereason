# TeleReason

TeleReason helps factories spot problems earlier. Overnight it scans robot-cell telemetry on one AMD Instinct MI300X and writes a morning report for maintenance technicians; a nightly gate checks what the data supports, so it reports verified facts, not guesses.

Built for the AMD Developer Hackathon ACT III, Track 1: Intelligent Industry.

> Work in progress. The demo link and the video will be added once they are recorded.

## Setup

Two processes, run separately.

### Backend (fake mode, no GPU needed)

```bash
cd backend
uv sync
cp ../.env.example .env   # ENGINE=fake by default
uv run uvicorn telereason.api.main:app --reload
```

Serves `/api` on `http://localhost:8000`, answering from `fixtures/`. See
[docs/api.md](docs/api.md) for the API contract. For the real engine
(`ENGINE=vllm`) on an AMD Instinct MI300X, see [infra/droplet.md](infra/droplet.md).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens on `http://localhost:5173` and proxies `/api` to the backend above.
See [frontend/DESIGN.md](frontend/DESIGN.md) for the UI conventions.

## Data

This repository contains no datasets or model weights. See [DATA_NOTICE.md](DATA_NOTICE.md).

To download the FactoryBench telemetry outside the repository:

```bash
cd backend
uv run python -m telereason.data.download
```

The destination is `DATA_DIR/factorybench` (`DATA_DIR` defaults to `./data`).
Existing Hugging Face files are reused, and `HF_TOKEN` may be set for
authenticated Hub access. Without downloaded data the API serves `fixtures/`
only when `ENGINE=fake`.

## License

[MIT](LICENSE)
