<!--
Copyright 2025-2026 Genesis Corporation

All Rights Reserved.

   Licensed under the Apache License, Version 2.0 (the "License"); you may
   not use this file except in compliance with the License. You may obtain
   a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
   License for the specific language governing permissions and limitations
   under the License.
-->

![Build workflow](https://github.com/infraguys/exordos_seed/actions/workflows/build.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)

# Exordos Seed OS

Exordos Seed OS is a diskless Linux-based system designed to boot before the main operating system. It performs initial hardware discovery, preparation, and provisioning tasks to get the primary system ready for deployment.

## What Exordos Seed OS Does

- **Hardware Discovery (Zoning)** - Probes and identifies all hardware components present on the host (CPU, memory, storage, network interfaces, etc.)
- **Image Provisioning** - Downloads and flashes the main operating system image to the target storage devices
- **Network Boot Support** - Boots over the network (PXE/iPXE) without requiring local disk storage
- **Pre-boot Environment** - Prepares the system before the main OS takes over

## About Exordos

Exordos Seed OS is part of the **Exordos** ecosystem — an open-source NoOps platform for managing corporate infrastructure and software ecosystems. Exordos provides a unified platform layer from bare metal and virtual machines to applications and services, designed to be operated by both humans and AI agents.

**📚 Exordos Documentation:** [exordos.github.io/exordos_core](https://exordos.github.io/exordos_core/)

# Building Exordos Seed OS

This repository contains build scripts for creating all components of the Seed OS.

## Prerequisites

- Linux build environment (Ubuntu/Debian recommended)
- Standard build tools: `build-essential`, `wget`, `git`
- Additional dependencies: `libzstd-dev`, `cpio`, `zstd`

## Build Everything

To build the complete Seed OS (kernel + initrd):

```sh
make
```

## Build Individual Components

### Kernel

Builds the Linux kernel (version 6.13.4) with custom configuration:

```sh
make kernel
```

The compiled kernel image will be available as `vmlinuz`.

### Initramfs (initrd)

Builds the initial RAM disk containing BusyBox, Python, and Seed OS agents:

```sh
make initrd
```

Output: `initrd.img` (compressed with zstd)

### Python

Builds a statically linked Python interpreter (version 3.14.3) for the initrd:

```sh
make python
```

Python is built with static libraries and includes native zstd compression support.

The **main Seed OS logic is implemented in Python** as an agent that runs during boot. The agent handles hardware discovery, communication with the Exordos control plane, and image provisioning.

**Source code location:** [`exordos_seed/`](exordos_seed/)

- [`exordos_seed/cmd/agent.py`](exordos_seed/cmd/agent.py) — Main agent entry point
- [`exordos_seed/drivers/`](exordos_seed/drivers/) — Hardware interaction drivers
- [`exordos_seed/common/`](exordos_seed/common/) — Shared utilities and orchestration logic

### iPXE Firmware

Builds iPXE boot firmware for network booting:

```sh
make ipxe
```

This creates:

- `undionly.kpxe` - BIOS/PCB boot firmware
- `virtio-net.rom` - ROM image for VirtIO network devices

An iPXE ROM only binds the PCI ID it was built for; on any other device it
silently drives no NIC, so the guest never sends DHCP. A virtio-net NIC reports
a different ID depending on the slot it lands in:

| guest NIC slot | guest PCI ID |
| --- | --- |
| PCIe root port — libvirt's default on q35, so all `exordos_core` guests | `1af4:1041` |
| plain PCI slot — i440fx guests, or q35 with no root port | `1af4:1000` |

The ID flips because QEMU's `disable-legacy=auto` drops legacy virtio support
for any device on a PCIe bus, which turns the NIC non-transitional. So that no
consumer has to work out which of the two its guests get, `virtio-net.rom`
carries an image for each and the BIOS runs whichever one matches the device.

To build only specific iPXE targets:

```sh
# BIOS boot only
make build_ipxe_bios

# EFI boot only
make build_ipxe_efi
```

# Cleaning Build Artifacts

To clean all build artifacts:

```sh
make clean
```

To clean specific components:

```sh
make clean_kernel     # Remove kernel sources and vmlinuz
make clean_python     # Remove Python build
make clean_ipxe       # Remove iPXE build
make clean_initrd     # Remove initrd directory and image
```
