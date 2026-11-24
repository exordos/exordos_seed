# Copyright 2025-2026 Genesis Corporation
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
KERNEL_VERSION="6.13.4"
KERNEL_URL="https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${KERNEL_VERSION}.tar.xz"
KERNEL_DIR="linux-${KERNEL_VERSION}"
KERNEL_TARBALL="linux-${KERNEL_VERSION}.tar.xz"
# Use half the number of cores
NPROC=$(shell echo "$(shell nproc)/2 + 1" | bc)

# There is an issue with building busybox on Ubuntu 24.04:
# https://github.com/gramineproject/gramine/tree/master/CI-Examples/busybox#note-on-ubuntu-2404-and-centos-stream-9
# Therefore we use binaries from busybox 1.35.0
BUSYBOX_VERSION="1.35.0"
BUSYBOX_URL="https://www.busybox.net/downloads/binaries/${BUSYBOX_VERSION}-x86_64-linux-musl/busybox"
RAMDISK_DIR="initrd"
RAMDISK_NAME="initrd.img"

# Python 3.14 is used for native zstd (compression.zstd) support.
PYTHON_VERSION="3.14.3"
PYTHON_PKG_NAME="exordos_seed"
PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz"
PYTHON_DIR="$(shell pwd)/Python-${PYTHON_VERSION}"
PYTHON_TARBALL="Python-${PYTHON_VERSION}.tar.xz"
PYTHON_OUTPUT_DIR="${PYTHON_DIR}/output"

IPXE_URL="https://github.com/ipxe/ipxe.git"
# Pinned on purpose: the ROM is firmware, and cloning master reships whatever
# upstream happened to have on build day — which is how a netboot regression
# arrived once already, with nothing changed on our side and no way to rebuild
# the artifact that shipped. Bump deliberately, after testing.
IPXE_REF="3ca799eb269c5df414c74b3ae7465ec726fb6372"
VIRTIO_ROM_NAME="virtio-net.rom"

# Default target
all: kernel initrd

clean: clean_kernel clean_busybox clean_python clean_initrd clean_ipxe

# Kernel part

kernel: download_kernel build_kernel
	cp linux-${KERNEL_VERSION}/arch/x86/boot/bzImage vmlinuz

rebuild_kernel: clean_kernel download_kernel build_kernel

download_kernel:
ifeq (,$(wildcard "${KERNEL_TARBALL}"))
	rm -f ${KERNEL_TARBALL}
endif
ifeq (,$(wildcard ${KERNEL_DIR}))
	rm -fr ${KERNEL_DIR}
endif
	wget ${KERNEL_URL}
	tar -xf linux-${KERNEL_VERSION}.tar.xz

build_kernel:
	cp configurations/kernel/kernel.cfg linux-${KERNEL_VERSION}/.config
	make -j${NPROC} -C linux-${KERNEL_VERSION}

clean_kernel:
	rm -fr ${KERNEL_DIR}
	rm -f ${KERNEL_TARBALL}
	rm -f vmlinuz

# Busybox part

busybox: download_busybox build_busybox

download_busybox: clean_busybox
	wget ${BUSYBOX_URL}

build_busybox:
	chmod +x busybox

clean_busybox:
	rm -f ./busybox

# Python part

python: download_python build_python

download_python: clean_python
	wget ${PYTHON_URL}
	tar -xf ${PYTHON_TARBALL}

build_python:
	rm -fr ${PYTHON_OUTPUT_DIR}
	mkdir ${PYTHON_OUTPUT_DIR}
# Building with static libraries and no shared libraries.
# LINKFORSHARED=" " suppresses -export-dynamic so the linker builds a
# fully static executable.
# Setup.local is processed first by makesetup and overrides Setup.stdlib,
# so our *static* declarations take priority over the default *shared* ones.
	cd ${PYTHON_DIR} && \
		./configure \
			LDFLAGS="-static -static-libgcc" \
			LINKFORSHARED=" " \
			LIBZSTD="-l:libzstd.a" \
			MODULE_BUILDTYPE=static \
			--disable-shared \
			--prefix=${PYTHON_OUTPUT_DIR} && \
		cp ../configurations/python/Setup ${PYTHON_DIR}/Modules/Setup.local && \
		make -j${NPROC} \
			Modules/_hacl/libHacl_Hash_MD5.a \
			Modules/_hacl/libHacl_Hash_SHA1.a \
			Modules/_hacl/libHacl_Hash_SHA2.a \
			Modules/_hacl/libHacl_Hash_SHA3.a \
			Modules/_hacl/libHacl_Hash_BLAKE2.a \
			Modules/_hacl/libHacl_HMAC.a && \
		make install -j${NPROC} \
			MODULE__MD5_LDFLAGS=Modules/_hacl/libHacl_Hash_MD5.a \
			MODULE__SHA1_LDFLAGS=Modules/_hacl/libHacl_Hash_SHA1.a \
			MODULE__SHA2_LDFLAGS=Modules/_hacl/libHacl_Hash_SHA2.a \
			MODULE__SHA3_LDFLAGS=Modules/_hacl/libHacl_Hash_SHA3.a \
			MODULE__BLAKE2_LDFLAGS=Modules/_hacl/libHacl_Hash_BLAKE2.a \
			MODULE__HMAC_LDFLAGS=Modules/_hacl/libHacl_HMAC.a \
			MODULE__ZSTD_LDFLAGS='-l:libzstd.a' ; \
		cd ..

clean_python:
	rm -fr ${PYTHON_DIR}
	rm -f ${PYTHON_TARBALL}

# IPXE part
#
# The ROM is built for x86_64 rather than i386 (iPXE's default `bin/` target).
# Firmware parks a virtio device's 64-bit BAR above 4 GiB as soon as the guest
# has RAM up there — past 2.75 GiB on q35, 3.5 GiB on i440fx — and a 32-bit
# iPXE cannot map an address it cannot represent, so it binds no NIC and the
# guest never sends DHCP. undionly.kpxe is built from the same directory, which
# is why the stock ROM chain never hit this.
#
# An iPXE ROM also only drives the PCI ID it was built for; on any other device
# it runs, binds no NIC and again emits no DHCP. A virtio-net NIC reports one of
# two IDs depending on the slot it lands in:
#
#   1af4:1041  non-transitional — a NIC on a PCIe root port, which is where
#              libvirt puts it on q35 (QEMU's disable-legacy=auto drops legacy
#              support on PCIe). This is what exordos_core's guests get.
#   1af4:1000  transitional — a NIC in a plain PCI slot: i440fx guests, or q35
#              without a root port. QEMU's own pxe-virtio.rom is this one.
#
# Rather than make every consumer figure out which of those its guests have, we
# ship one ROM holding an image for each: catrom concatenates them into a single
# multi-image option ROM and the BIOS runs whichever image matches the device.

ipxe: download_ipxe build_ipxe_bios build_ipxe_virtio

download_ipxe: clean_ipxe
	git clone ${IPXE_URL}
	cd ipxe && git checkout ${IPXE_REF}

build_ipxe_bios:
	cp configurations/IPXE/pcbios.ipxe ipxe/src/
	cd ipxe/src/ && \
		make -j${NPROC} bin-x86_64-pcbios/undionly.kpxe EMBED=pcbios.ipxe
	cp ipxe/src/bin-x86_64-pcbios/undionly.kpxe .

build_ipxe_efi:
	cp configurations/IPXE/uefi.ipxe ipxe/src/
	cd ipxe/src/ && \
		make -j${NPROC} bin-x86_64-efi/ipxe.efi EMBED=uefi.ipxe

build_ipxe_virtio:
	cp configurations/IPXE/netboot.ipxe ipxe/src/
	cd ipxe/src/ && \
		make -j${NPROC} bin-x86_64-pcbios/1af41000.rom EMBED=netboot.ipxe && \
		make -j${NPROC} bin-x86_64-pcbios/1af41041.rom EMBED=netboot.ipxe && \
		perl util/catrom.pl bin-x86_64-pcbios/1af41000.rom \
			bin-x86_64-pcbios/1af41041.rom > ../../${VIRTIO_ROM_NAME}

clean_ipxe:
	rm -fr ipxe
	rm -f undionly.kpxe
	rm -f ${VIRTIO_ROM_NAME}

# Initramfs part
initrd: busybox python build_initrd

rebuild_initrd: clean_initrd build_initrd

build_initrd:
# Prepare structure of the initramfs
	mkdir ${RAMDISK_DIR}
	cd ${RAMDISK_DIR} && \
		mkdir dev etc proc sys tmp lib mnt var bin
	cp init ${RAMDISK_DIR}/init
	cp bin/ifup.sh ${RAMDISK_DIR}/bin/
	chmod +x ${RAMDISK_DIR}/init
	chmod +x ${RAMDISK_DIR}/bin/ifup.sh
	./busybox --install ${RAMDISK_DIR}/bin
# Install Python
	cp -r ${PYTHON_OUTPUT_DIR}/lib/* ${RAMDISK_DIR}/lib/
	cp -L ${PYTHON_OUTPUT_DIR}/bin/python3 ${RAMDISK_DIR}/bin/
# Remove build-only artifacts not needed at runtime
	rm -f  ${RAMDISK_DIR}/lib/libpython3.14.a
	rm -rf ${RAMDISK_DIR}/lib/pkgconfig
	rm -rf ${RAMDISK_DIR}/lib/python3.14/config-3.14-x86_64-linux-gnu
	rm -rf ${RAMDISK_DIR}/lib/python3.14/test
	rm -rf ${RAMDISK_DIR}/lib/python3.14/idlelib
	rm -rf ${RAMDISK_DIR}/lib/python3.14/ensurepip
	rm -rf ${RAMDISK_DIR}/lib/python3.14/tkinter
	find ${RAMDISK_DIR}/lib/python3.14 -name '__pycache__' -type d -exec rm -rf {} +
# Install Python packages
	mkdir ${RAMDISK_DIR}/lib/python3.14/site-packages/${PYTHON_PKG_NAME}
	cp -r ${PYTHON_PKG_NAME} ${RAMDISK_DIR}/lib/python3.14/site-packages/${PYTHON_PKG_NAME}/
	cd ${RAMDISK_DIR}/lib/python3.14/site-packages/${PYTHON_PKG_NAME}/ && \
		find . -name '*.py[co]' -delete && \
		find . -name __pycache__ -type d -exec rm -rf {} +
# Add CA certificates
	mkdir -p ${RAMDISK_DIR}/etc/ssl/certs
	cp /etc/ssl/certs/ca-certificates.crt ${RAMDISK_DIR}/etc/ssl/certs/
# Build initramfs
	cd ${RAMDISK_DIR} && \
		find . | cpio -H newc -o | zstd -19 -T0 >../${RAMDISK_NAME}

clean_initrd:
	rm -fr ${RAMDISK_DIR}
	rm -f ${RAMDISK_NAME}

.PHONY: kernel clean build_kernel clean_kernel download_kernel rebuild_kernel \
	    busybox download_busybox build_busybox clean_busybox \
	    python download_python build_python clean_python \
	    ipxe download_ipxe build_ipxe_bios build_ipxe_virtio clean_ipxe \
	    clean_initrd build_initrd rebuild_initrd initrd all
