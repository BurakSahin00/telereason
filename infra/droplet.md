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
| Downloads | In the background inside the container: `openai/gpt-oss-120b` (about 65 GB; the repo's `original/` and `metal/` copies are skipped) into the Hugging Face cache, `FactoryBench/FactoryBench` into `/root/data/factorybench`. Logs in `/root/logs/` | `model download started`, `dataset download started` |

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

## Check from your laptop

A container port must not answer from outside:

```powershell
Test-NetConnection <droplet-address> -Port 8888    # Windows: TcpTestSucceeded : False
```

```bash
nc -vz -w 5 <droplet-address> 8888                 # macOS/Linux: the connection fails or times out
```

SSH (port 22) must still answer.

## End of session

Destroy the droplet in the AMD Developer Cloud panel. Powering it off is not enough: it keeps its resources reserved. Model and data are downloaded again next time. Before destroying it, copy any results that must be kept to your laptop (`scp`) and add them to `results/` through a pull request.

## Not covered yet

- Running the app on the droplet (engine, API, demo URL) comes with the real-engine and deployment issues. Our `uv.lock` pins versions resolved on a laptop, while the container brings its own versions of shared packages such as FastAPI; those must be reconciled there, not by upgrading the container.
- If a dataset outside FactoryBench is needed, its download is added to the script.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ROCm …, expected 7.2.3` | The image changed. Stop; the XGBoost wheel must be rebuilt for the new ROCm before the gate can run on the GPU. |
| `xgboost … on cpu` or an import error about `libomp` | The script installs `libomp-dev`; if it still fails, add `/opt/rocm/lib/llvm/lib` to `LD_LIBRARY_PATH` and run the script again. |
| `password login is still enabled` | Another file in `/etc/ssh/sshd_config.d/` sets it first; check `sshd -T` and the files there. |
| A download stops | `bash setup_droplet.sh status`, then run the script again: a download that is still running is left alone, a stopped one restarts and resumes where it stopped. |
