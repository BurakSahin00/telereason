# TeleReason

TeleReason helps factories spot problems earlier. Overnight it scans robot-cell telemetry on one AMD Instinct MI300X and writes a morning report for maintenance technicians; a nightly gate checks what the data supports, so it reports verified facts, not guesses.

Built for the AMD Developer Hackathon ACT III, Track 1: Intelligent Industry.

> Work in progress. The demo link, the video and the setup instructions will be added during the hackathon week.

## Data

This repository contains no datasets or model weights. See [DATA_NOTICE.md](DATA_NOTICE.md).

To download the FactoryBench telemetry outside the repository:

```bash
cd backend
uv run python -m telereason.data.download
```

The destination is `DATA_DIR` (default `./data`). Existing Hugging Face files
are reused, and `HF_TOKEN` may be set for authenticated Hub access.

## License

[MIT](LICENSE)
