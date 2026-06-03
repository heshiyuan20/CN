# Runtime Environment Setup

This project needs a Linux environment with:

- Mininet
- Open vSwitch with kernel support
- Python 3.8
- `os-ken<4` and `eventlet==0.29.1`

## Important Limitation

The current WSL2 machine used during development cannot run the full Mininet + Open vSwitch stack for this project, because the kernel does not provide the `openvswitch` module.

Use a normal Ubuntu virtual machine instead.

## Recommended Platform

- Ubuntu 20.04 or Ubuntu 24.04 VM
- Native Linux kernel or VM kernel with Open vSwitch support

## One-Command Setup

From the project root, run:

```bash
bash setup_vm_env.sh
```

This script installs:

- system packages for Mininet and Open vSwitch
- `arping`
- Miniconda
- a `cs305` Python 3.8 conda environment
- all Python dependencies from `requirements.txt`

## Post-Setup Checks

Verify the Mininet baseline first:

```bash
sudo mn -c
sudo mn --test pingall
```

If this check fails, do not continue to project testing. Fix the VM kernel / OVS setup first.

## Starting the Controller

```bash
conda activate cs305
cd /path/to/CS305-2026Spring-Project
osken-manager --observe-links controller.py
```

## Running Tests

DHCP:

```bash
cd tests/dhcp_test
sudo env "PATH=$PATH" python test_network.py
```

Shortest-path switching:

```bash
cd tests/switching_test
sudo env "PATH=$PATH" python test_network.py
```

Firewall:

```bash
cd tests/firewall_test
sudo env "PATH=$PATH" python test_network.py
```

Complex topology demo:

```bash
cd tests/complex_topology_test
sudo env "PATH=$PATH" python test_network.py
```

## Expected Result

You should be able to:

- start `osken-manager` successfully
- run Mininet test scripts without `mnexec` or OVS startup failures
- use `pingall` in Mininet CLI
- see shortest-path logs printed by the controller
- verify firewall behavior from the provided test script