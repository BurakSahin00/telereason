# MI300X droplet

The demo, the real engine and the gate run on one AMD Instinct MI300X droplet from AMD Developer Cloud. The droplet is disposable: everything it needs comes from this repository, a GitHub Release or a download, so it is rebuilt with one script and destroyed after each work session. No code is edited on the droplet; it only pulls `main` or a tag.

## Before you start

- A droplet from the **vLLM quick-start image** with one MI300X. The image runs a Docker container named `rocm` with ROCm 7.2.3, Python 3.12, PyTorch and vLLM.
- Your SSH key added when the droplet is created. Teammates log in with the public keys on their GitHub accounts (`https://github.com/<user>.keys`); the script adds them. Private keys are never shared.
- Optional: a Hugging Face token. The script asks for it, keeps it in memory for the downloads, and never writes it to a file. Do not paste it into a command line, a file or a chat.

The first SSH login can answer "Please wait while we get your droplet ready" for a few minutes after creation; wait and retry.

## Set up

On the droplet host (not inside the container), as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/BurakSahin00/telereason/main/infra/setup_droplet.sh
less setup_droplet.sh            # read it before running it
bash setup_droplet.sh            # or: --ref v0.1, --team "user1 user2", --skip-model, --skip-data
bash setup_droplet.sh status     # follow the model and data downloads
```

The script is safe to run again. Run it again after a reboot (the Docker port rules do not survive one).

## What the script does

| Step | What | Expected output |
|---|---|---|
| Firewall | `ufw` allows only OpenSSH; the image's 80/443 rules are removed. Docker-published ports bypass `ufw`, so inbound traffic to the container ports 8000, 8091, 8888 and 30000 is dropped in the `DOCKER-USER` chain | `ufw allows: OpenSSH` |
| SSH | Password login off, root login with keys only. A drop-in file `01-telereason.conf` is written only if password login was on | `password login disabled` |
| Team keys | Public keys from GitHub for the users in `--team` (default: `buraksal52`) are added to `authorized_keys`, without duplicates. A teammate first adds a public key under GitHub → Settings → SSH and GPG keys | `<user>: N new key(s)`, or a warning if the user has no public key on GitHub |
| Versions | ROCm and Python must match the XGBoost build (the script stops otherwise); torch and vLLM are checked and never upgraded | `ROCm 7.2.3, Python 3.12, torch 2.11.0+…, vLLM 0.27.1+…` |
| XGBoost | Our ROCm build from the GitHub Release (below), checked by SHA-256, installed with `--no-deps`; then a short training on the GPU | `xgboost 3.2.0 on cuda:0` |
| Python extras | `scikit-learn` (the gate's AUROC) and the `hf` CLI if missing; every package already in the image is pinned as a constraint, so nothing there changes | `scikit-learn …, hf CLI present` |
| Repository | `/root/telereason` inside the container at `--ref` (default `main`), shallow, detached | `repo at main: <commit>` |
| Downloads | In the background inside the container: `openai/gpt-oss-120b` (about 65 GB; the repo's `original/` and `metal/` copies are skipped) into the Hugging Face cache, `FactoryBench/FactoryBench` at the pinned revision `DATASET_REVISION` (the same as `DEFAULT_REVISION` in `backend/telereason/data/download.py`), only the files the data loader reads (`DEFAULT_PATTERNS` there), into `/root/data/factorybench`. Logs in `/root/logs/` | `model download started`, `dataset download started` |

Inside the container, `DATA_DIR` is `/root/data`.

## XGBoost for ROCm

AMD's prebuilt `amd_xgboost` wheels need Ubuntu 24.04 and the container is Ubuntu 22.04, so we build XGBoost ourselves and publish the wheel as a GitHub Release asset:

- Release: [`xgboost-rocm-gfx942`](https://github.com/BurakSahin00/telereason/releases/tag/xgboost-rocm-gfx942)
- File: `amd_xgboost-3.2.0-py3-none-linux_x86_64.whl`
- SHA-256: `511377cea997c29f548b74c194f9bd1d7e0da7a48674a4f48662e9dfb94790b9`
- Built from [AMD-Ecosystem/xgboost](https://github.com/AMD-Ecosystem/xgboost) (Apache License 2.0), unmodified, with `USE_HIP=1` and `AMDGPU_TARGETS=gfx942`, inside the same container. On the GPU, XGBoost reports its device as `cuda:0`.

Installed by hand it is:

```bash
pip install --no-deps amd_xgboost-3.2.0-py3-none-linux_x86_64.whl
```

The wheel is only rebuilt if the image's ROCm version changes; a rebuild gets a new release tag and a new checksum here, never a replaced file.

## Run the app

The app is one process: FastAPI with the vLLM engine inside it. The engine cannot be shared across processes, so uvicorn always runs with `--workers 1`. The app runs inside the container, listens only on the droplet itself, and is reached from your laptop through an SSH tunnel. Run these on the droplet host.

1. **How the container is networked.** This decides the listen address in step 3.

   ```bash
   docker inspect -f '{{.HostConfig.NetworkMode}}' rocm
   ```

   `host`: listen on `127.0.0.1`. Anything else (a bridge with published ports): listen on `0.0.0.0` inside the container; the app is then reached through the published port 8000 on the droplet, which the `DOCKER-USER` rule closes to the outside.

2. **Python packages.** The image brings FastAPI and uvicorn with vLLM. Check the rest; install only what is missing, with the image's packages as constraints so nothing there changes:

   ```bash
   docker exec rocm python -c 'import fastapi, uvicorn, pyarrow, huggingface_hub'
   docker exec rocm bash -c 'pip install -q --root-user-action=ignore -c <(pip freeze) pyarrow'   # only if pyarrow was missing
   ```

3. **Frontend.** The app serves the built frontend from the same origin. Build it on the droplet host and copy it into the container (see [Frontend in the ROCm container](#frontend-in-the-rocm-container)).

4. **Start.** `HF_HUB_OFFLINE=1` makes vLLM load the downloaded model and fail clearly if the download is not finished. `FRONTEND_DIST` points at the frontend from step 3; leave it out to run the API only.

   ```bash
   docker exec -d -w /root/telereason/backend \
     -e ENGINE=vllm -e NIGHT_SOURCE=stored -e DATA_DIR=/root/data -e HF_HUB_OFFLINE=1 \
     -e FRONTEND_DIST=/root/telereason/frontend/dist rocm \
     bash -c 'python -m uvicorn telereason.api.main:app --workers 1 --host 127.0.0.1 --port 8000 > /root/logs/app.log 2>&1'
   ```

   With a bridge network, use `--host 0.0.0.0`. If `FRONTEND_DIST` has no `index.html`, the app stops at startup with an error naming `FRONTEND_DIST`.

5. **Follow the startup.** The API answers at once with `ready: false`; the engine loads in the background, then sends one short warm-up request.

   ```bash
   docker exec rocm tail -f /root/logs/app.log
   docker exec rocm curl -s 127.0.0.1:8000/api/health     # in a second shell
   ```

   | Log line | Meaning |
   |---|---|
   | `Application startup complete` | The API answers; `/api/health` says `"ready": false` |
   | `vllm engine started in … s` | Weights loaded; the startup time |
   | `vllm engine ready in … s …; warm-up: … ms, … completion tokens` followed by the warm-up text | `/api/health` says `"ready": true` |
   | `vllm engine failed to start` with a traceback | The app keeps running with `"ready": false`; fix the cause and restart |

   Until the engine is ready, endpoints that need it answer `503` with `{"error": {"code": "engine_not_ready", …}}`.

6. **Open it from your laptop** through an SSH tunnel, then use `http://localhost:8000` (the frontend), `http://localhost:8000/api/health` and `http://localhost:8000/docs`:

   ```bash
   ssh -N -L 8000:127.0.0.1:8000 root@<droplet-address>
   ```

7. **Stop.** A graceful stop closes the engine (`vllm engine closed` in the log). A stop during loading returns only after the weights have finished loading.

   ```bash
   docker exec rocm pkill -INT -f 'uvicorn telereason.api.main:app'
   ```

## Frontend in the ROCm container

In real mode the app keeps running in the existing `rocm` container (the vLLM ROCm image); the root `Dockerfile` is the fake-mode container for laptops. The droplet takes only its `frontend-dist` stage, which holds nothing but the built files, so no Node.js is installed in the container. Run on the droplet host, with the same ref as the repository in the container (`main` here; a tag for the demo):

1. Build the frontend (needs BuildKit, the default builder since Docker 23):

   ```bash
   docker build --target frontend-dist --output type=local,dest=/root/frontend-dist "https://github.com/BurakSahin00/telereason.git#main"
   ```

2. Copy it into the container:

   ```bash
   docker cp /root/frontend-dist/. rocm:/root/telereason/frontend/dist
   ```

3. Start the app with `-e FRONTEND_DIST=/root/telereason/frontend/dist`, as in step 4 of [Run the app](#run-the-app).

Repeat the three steps after the repository in the container moves to another ref.

## Check from your laptop

A container port must not answer from outside:

```powershell
Test-NetConnection <droplet-address> -Port 8888    # Windows: TcpTestSucceeded : False
```

```bash
nc -vz -w 5 <droplet-address> 8888                 # macOS/Linux: the connection fails or times out
```

SSH (port 22) must still answer. While the app runs, port 8000 must not answer from outside either; it is reached only through the SSH tunnel.

## End of session

Destroy the droplet in the AMD Developer Cloud panel. Powering it off is not enough: it keeps its resources reserved. Model and data are downloaded again next time. Before destroying it, copy any results that must be kept to your laptop (`scp`) and add them to `results/` through a pull request.

## Not covered yet

- The demo URL (Cloudflare tunnel or Caddy) and a systemd service that restarts the app come with the deployment issue. Our `uv.lock` pins versions resolved on a laptop, while the container brings its own versions of shared packages such as FastAPI; those must be reconciled there, not by upgrading the container.
- If a dataset outside FactoryBench is needed, its download is added to the script.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ROCm …, expected 7.2.3` | The image changed. Stop; the XGBoost wheel must be rebuilt for the new ROCm before the gate can run on the GPU. |
| `xgboost … on cpu` or an import error about `libomp` | The script installs `libomp-dev`; if it still fails, add `/opt/rocm/lib/llvm/lib` to `LD_LIBRARY_PATH` and run the script again. |
| `password login is still enabled` | Another file in `/etc/ssh/sshd_config.d/` sets it first; check `sshd -T` and the files there. |
| A download stops | `bash setup_droplet.sh status`, then run the script again: a download that is still running is left alone, a stopped one restarts and resumes where it stopped. |
