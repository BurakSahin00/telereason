#!/usr/bin/env bash
# TeleReason: prepare a fresh AMD Developer Cloud MI300X droplet (vLLM quick-start image).
#
# Run as root on the droplet host, not inside the container:
#   bash setup_droplet.sh [--ref <branch-or-tag>] [--team "<github-user> ..."] [--skip-model] [--skip-data]
#   bash setup_droplet.sh status
#
# Safe to run again. It never writes the Hugging Face token to disk.
# Steps are documented in infra/droplet.md.

set -euo pipefail

# ---- settings ---------------------------------------------------------------
CONTAINER="rocm"
REPO_URL="https://github.com/BurakSahin00/telereason.git"
REF="main"
TEAM_USERS="buraksal52"                 # GitHub users whose public SSH keys may log in
BLOCK_PORTS="8000 8091 8888 30000"      # ports the image publishes from the container
SKIP_MODEL=0
SKIP_DATA=0

EXPECT_ROCM="7.2.3"
EXPECT_PYTHON="3.12"
EXPECT_VLLM="0.27.1"
EXPECT_TORCH="2.11.0"

MODEL_ID="openai/gpt-oss-120b"
DATASET_ID="FactoryBench/FactoryBench"
XGB_WHEEL="amd_xgboost-3.2.0-py3-none-linux_x86_64.whl"
XGB_URL="https://github.com/BurakSahin00/telereason/releases/download/xgboost-rocm-gfx942/${XGB_WHEEL}"
XGB_SHA256="511377cea997c29f548b74c194f9bd1d7e0da7a48674a4f48662e9dfb94790b9"

log()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '    ok: %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf '    ERROR: %s\n' "$*" >&2; exit 1; }

# ---- status -----------------------------------------------------------------
if [[ "${1:-}" == "status" ]]; then
  docker exec "$CONTAINER" bash -c '
    for f in /root/logs/*.log; do [ -e "$f" ] && { echo "--- $f"; tail -n 3 "$f"; }; done
    echo "--- sizes"; du -sh /root/.cache/huggingface /root/data 2>/dev/null || true
    echo "--- repo"; git -C /root/telereason log -1 --oneline 2>/dev/null || echo "not cloned"'
  exit 0
fi

# ---- arguments --------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)        REF="$2"; shift 2 ;;
    --team)       TEAM_USERS="$2"; shift 2 ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --skip-data)  SKIP_DATA=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run as root on the droplet host"
command -v docker >/dev/null || die "docker not found; is this the vLLM quick-start image?"
docker ps --format '{{.Names}}' | grep -x "$CONTAINER" >/dev/null || die "container '$CONTAINER' is not running (docker ps)"

# ---- 1. firewall: only SSH --------------------------------------------------
log "Firewall: only SSH from outside"
ufw allow OpenSSH >/dev/null
for rule in 80/tcp 443/tcp 80 443; do ufw --force delete allow "$rule" >/dev/null 2>&1 || true; done
ufw --force enable >/dev/null
ok "ufw allows: $(ufw status | awk '/ALLOW/ {print $1}' | sort -u | tr '\n' ' ')(expected: OpenSSH only)"

# Docker-published ports bypass ufw, so drop them in the DOCKER-USER chain.
# These rules do not survive a reboot; run this script again after one.
for p in $BLOCK_PORTS; do
  if ! iptables -C DOCKER-USER -p tcp -m conntrack --ctorigdstport "$p" --ctdir ORIGINAL -j DROP 2>/dev/null; then
    iptables -I DOCKER-USER -p tcp -m conntrack --ctorigdstport "$p" --ctdir ORIGINAL -j DROP
  fi
done
ok "DOCKER-USER drops inbound traffic to ports: $BLOCK_PORTS"

# ---- 2. SSH: keys only --------------------------------------------------------
log "SSH: keys only"
mkdir -p /run/sshd   # sshd -T needs it when ssh is socket-activated
sshd_cfg="$(sshd -T)"
if grep -qi '^passwordauthentication yes' <<< "$sshd_cfg"; then
  # sshd keeps the first value it reads; 01- sorts before cloud-init's 50- file.
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/01-telereason.conf
  sshd -t || die "sshd config test failed; check /etc/ssh/sshd_config.d/01-telereason.conf"
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
fi
sshd_cfg="$(sshd -T)"
grep -qi '^passwordauthentication no' <<< "$sshd_cfg" || die "password login is still enabled"
ok "password login disabled"

# ---- 3. teammates' public keys from GitHub ----------------------------------
log "Teammates' public SSH keys"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
key_re='^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) '
for user in $TEAM_USERS; do
  keys="$(curl -fsSL "https://github.com/${user}.keys" || true)"
  if [[ -z "$keys" ]]; then warn "no public keys on GitHub for ${user}"; continue; fi
  added=0
  while IFS= read -r key; do
    [[ "$key" =~ $key_re ]] || continue
    grep -qxF "$key" /root/.ssh/authorized_keys || { printf '%s\n' "$key" >> /root/.ssh/authorized_keys; added=$((added + 1)); }
  done <<< "$keys"
  ok "${user}: ${added} new key(s)"
done

# ---- 4. Hugging Face token (optional, memory only) ----------------------------
if [[ -z "${HF_TOKEN:-}" && ( $SKIP_MODEL -eq 0 || $SKIP_DATA -eq 0 ) ]]; then
  read -rs -p "Hugging Face token (Enter to skip; it is not saved): " HF_TOKEN || true
  echo
fi
export HF_TOKEN="${HF_TOKEN:-}"

# ---- 5. inside the container --------------------------------------------------
log "Container '$CONTAINER'"
docker exec -i -e HF_TOKEN -e REF="$REF" -e REPO_URL="$REPO_URL" \
  -e SKIP_MODEL="$SKIP_MODEL" -e SKIP_DATA="$SKIP_DATA" \
  -e EXPECT_ROCM="$EXPECT_ROCM" -e EXPECT_PYTHON="$EXPECT_PYTHON" \
  -e EXPECT_VLLM="$EXPECT_VLLM" -e EXPECT_TORCH="$EXPECT_TORCH" \
  -e MODEL_ID="$MODEL_ID" -e DATASET_ID="$DATASET_ID" \
  -e XGB_WHEEL="$XGB_WHEEL" -e XGB_URL="$XGB_URL" -e XGB_SHA256="$XGB_SHA256" \
  "$CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
ok()   { printf '    ok: %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf '    ERROR: %s\n' "$*" >&2; exit 1; }
[[ -n "${HF_TOKEN:-}" ]] || unset HF_TOKEN

# versions: ROCm and Python must match the XGBoost build; torch and vLLM are never upgraded here
rocm="$(cat /opt/rocm/.info/version 2>/dev/null || echo unknown)"
py="$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
read -r torch_v vllm_v < <(python -c 'from importlib.metadata import version as v; print(v("torch"), v("vllm"))')
[[ "$rocm" == "$EXPECT_ROCM"* ]] || die "ROCm $rocm, expected $EXPECT_ROCM (the XGBoost wheel is built for it)"
[[ "$py" == "$EXPECT_PYTHON" ]] || die "Python $py, expected $EXPECT_PYTHON"
[[ "$torch_v" == "$EXPECT_TORCH"* ]] || warn "torch $torch_v, expected $EXPECT_TORCH"
[[ "$vllm_v" == "$EXPECT_VLLM"* ]] || warn "vLLM $vllm_v, expected $EXPECT_VLLM"
ok "ROCm $rocm, Python $py, torch $torch_v, vLLM $vllm_v"

# system packages: git for the repo, OpenMP runtime for XGBoost
if ! command -v git >/dev/null || ! ldconfig -p | grep 'libomp\.so' >/dev/null; then
  # apt prints harmless warnings about the image's legacy ROCm repository keys
  apt-get update -qq </dev/null 2>&1 | grep -v 'legacy trusted.gpg' || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git libomp-dev </dev/null >/dev/null 2>&1 \
    || die "apt-get install git libomp-dev failed"
fi
ok "git and libomp present"

# XGBoost: our ROCm build from the GitHub Release, checked by sha256, installed without dependencies
if ! python -c 'import xgboost, sys; sys.exit(0 if xgboost.__version__ == "3.2.0" else 1)' 2>/dev/null; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/$XGB_WHEEL" "$XGB_URL"
  echo "$XGB_SHA256  $tmp/$XGB_WHEEL" | sha256sum -c --quiet - || die "XGBoost wheel checksum mismatch"
  pip install -q --root-user-action=ignore --no-deps "$tmp/$XGB_WHEEL"
  rm -f "$tmp/$XGB_WHEEL" && rmdir "$tmp"
fi
python - <<'PY' || exit 1
import json, sys
import numpy as np, xgboost as xgb
X = np.random.rand(2000, 20); y = (X[:, 0] > 0.5).astype(int)
b = xgb.train({"tree_method": "hist", "device": "cuda", "objective": "binary:logistic"},
              xgb.DMatrix(X, label=y), num_boost_round=10)
dev = json.loads(b.save_config())["learner"]["generic_param"]["device"]
print(f"    ok: xgboost {xgb.__version__} on {dev}")
sys.exit(0 if dev.startswith("cuda") else 1)
PY

# extra Python packages: everything already in the image is pinned, so nothing there is upgraded
pip list --format=freeze > /tmp/container-constraints.txt
python -c 'import sklearn' 2>/dev/null || pip install -q --root-user-action=ignore scikit-learn -c /tmp/container-constraints.txt
command -v hf >/dev/null || pip install -q --root-user-action=ignore "huggingface_hub[cli]" -c /tmp/container-constraints.txt
ok "scikit-learn $(python -c 'import sklearn; print(sklearn.__version__)'), hf CLI present"

# repository at the requested ref (the droplet only pulls; no code is edited here)
if [[ -d /root/telereason/.git ]]; then
  git -C /root/telereason fetch -q --depth 1 origin "$REF"
  git -C /root/telereason checkout -q --detach FETCH_HEAD
else
  git clone -q --depth 1 --branch "$REF" "$REPO_URL" /root/telereason
fi
ok "repo at $REF: $(git -C /root/telereason log -1 --oneline)"

# model and data downloads run in the background; follow them with: bash setup_droplet.sh status
mkdir -p /root/logs /root/data
running() { [[ -f "/root/logs/$1.pid" ]] && kill -0 "$(cat "/root/logs/$1.pid")" 2>/dev/null; }
if [[ "$SKIP_MODEL" == "0" ]]; then
  if running model; then ok "model download already running"; else
    # the repo also holds a second copy of the weights (original/) and a Metal build (metal/); vLLM needs neither
    nohup hf download "$MODEL_ID" --exclude "original/*" --exclude "metal/*" </dev/null > /root/logs/model.log 2>&1 &
    echo $! > /root/logs/model.pid
    ok "model download started ($MODEL_ID, about 65 GB) -> /root/logs/model.log"
  fi
fi
if [[ "$SKIP_DATA" == "0" ]]; then
  if running data; then ok "dataset download already running"; else
    nohup hf download "$DATASET_ID" --repo-type dataset --local-dir /root/data/factorybench </dev/null > /root/logs/data.log 2>&1 &
    echo $! > /root/logs/data.pid
    ok "dataset download started ($DATASET_ID) -> /root/data/factorybench, /root/logs/data.log"
  fi
fi
IN_CONTAINER

log "Done. Downloads continue in the background: bash setup_droplet.sh status"
echo "    From your laptop, check that a container port is closed, for example port 8888 (see infra/droplet.md)."
echo "    At the end of the session, destroy the droplet in the AMD Developer Cloud panel."
