#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINICONDA_DIR="${HOME}/software/miniconda3"
ENV_NAME="cs305"

echo "[1/6] Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  mininet \
  openvswitch-switch \
  arping \
  build-essential \
  python3-dev \
  libxml2-dev \
  libxslt1-dev \
  zlib1g-dev \
  pkg-config \
  git \
  curl \
  wget

echo "[2/6] Checking Mininet baseline"
sudo mn -c || true

if ! command -v mn >/dev/null 2>&1; then
  echo "Mininet installation failed: 'mn' not found" >&2
  exit 1
fi

if [[ ! -d "${MINICONDA_DIR}" ]]; then
  echo "[3/6] Installing Miniconda"
  tmp_installer="$(mktemp)"
  wget -O "${tmp_installer}" https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
  bash "${tmp_installer}" -b -p "${MINICONDA_DIR}"
  rm -f "${tmp_installer}"
fi

echo "[4/6] Preparing conda environment"
export PATH="${MINICONDA_DIR}/bin:${PATH}"
eval "$(conda shell.bash hook)"

if ! conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  conda create -y -n "${ENV_NAME}" python=3.8
fi

conda activate "${ENV_NAME}"

echo "[5/6] Installing Python dependencies"
cd "${PROJECT_DIR}"
pip install --upgrade pip
pip install -r requirements.txt

echo "[6/6] Verifying controller runtime"
python --version
osken-manager --version

cat <<EOF

Environment setup finished.

Next steps:
1. Run 'sudo mn --test pingall' and confirm it succeeds.
2. Start the controller:
   conda activate ${ENV_NAME}
   cd ${PROJECT_DIR}
   osken-manager --observe-links controller.py
3. In another terminal, run one of:
   cd ${PROJECT_DIR}/tests/dhcp_test && sudo env "PATH=\$PATH" python test_network.py
   cd ${PROJECT_DIR}/tests/switching_test && sudo env "PATH=\$PATH" python test_network.py
   cd ${PROJECT_DIR}/tests/firewall_test && sudo env "PATH=\$PATH" python test_network.py
   cd ${PROJECT_DIR}/tests/complex_topology_test && sudo env "PATH=\$PATH" python test_network.py

EOF