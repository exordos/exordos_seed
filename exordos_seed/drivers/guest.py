#    Copyright 2025 Genesis Corporation.
#
#    All Rights Reserved.
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
from http import client as http_client
import logging
import os
import random
import subprocess
import sys
import time
import typing as tp
import urllib.error

from exordos_seed.common import constants as c
from exordos_seed.common import utils
from exordos_seed.common.http import base as http
from exordos_seed.common.orch import core
from exordos_seed.dm import models

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
DEFAULT_BLOCK_DEVICE = "/dev/vda"
KIND = "guest_machine"
SHA256SUM_SUFFIX = ".SHA256SUM"
# Network errors after which a download is retried. Socket timeouts during
# a read raise TimeoutError, not URLError, so they are listed explicitly.
RETRIABLE_DOWNLOAD_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http_client.HTTPException,
)

# Type alias for both client types
CoreClientType = tp.Union[core.CoreClient, core.AutonomousCoreClient]


def ro_opener(path, flags):
    return os.open(path, flags, 0o400)


def render_progress_bar(percent: int, width: int = 30) -> str:
    """Return simple ASCII progress bar string for the given percent."""
    clamped = max(0, min(100, percent))
    filled = int(width * clamped / 100)
    bar = "=" * filled + "-" * (width - filled)
    return f"[{bar}] {clamped:3d}%"


def display_progress_line(percent: int, written_bytes: int) -> None:
    """Write progress bar on a single console line using carriage return."""
    bar = render_progress_bar(percent)
    written_mib = written_bytes / 1024**2
    sys.stdout.write(f"\rFlashing progress: {bar}, {written_mib:.2f} MiB written")
    sys.stdout.flush()


class GuestCapDriver:
    FINISH_FLAG_PATH = "/seed_os_finished"

    def __init__(self):
        self._machine = None

    def _is_ready(self) -> bool:
        return os.path.exists(self.FINISH_FLAG_PATH)

    def _mark_ready(self):
        with open(self.FINISH_FLAG_PATH, "w") as f:
            f.write("")

    def _reboot(self) -> None:
        subprocess.run("/bin/sh -c '(sleep 1 && reboot -f)&'", shell=True)

    def _shutdown(self) -> None:
        subprocess.run("/bin/sh -c '(sleep 1 && poweroff -f)&'", shell=True)

    def _gen_hash(self) -> str:
        return str(random.randint(0, 100_000_000))

    def _download_image(
        self,
        image_url: str,
        destination_path: str,
    ) -> None:
        expected_sha256 = None
        checksum_url = image_url + SHA256SUM_SUFFIX
        progress = 0

        # Handler for progress tracking
        def handler(total: int, read: int, written: int, chunk: bytes):
            nonlocal progress
            if total == 0:
                LOG.warning("Flashing progress: %d MiB written", written / 1024**2)
                return
            current_progress = int((read / total) * 100)
            if current_progress > progress:
                progress = current_progress
                display_progress_line(progress, written)

        while True:
            try:
                checksum_bytes = http.stream_to_bytes(checksum_url)
                checksum_text = checksum_bytes.decode("utf-8").strip()
                if checksum_text:
                    expected_sha256 = checksum_text.split()[0].lower()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    LOG.warning("SHA256SUM file not found, continue without checksum")
                else:
                    raise
            except RETRIABLE_DOWNLOAD_ERRORS:
                # Use random timeout to avoid thundering herd
                timeout = random.randint(5, 60)
                LOG.exception(
                    "SHA256SUM download failed, retrying in %d seconds...", timeout
                )
                time.sleep(timeout)
                continue

            break

        # Download the image with retry logic until it succeeds and (optionally)
        # the checksum matches the expected SHA256 value from the .SHA256SUM file.
        # On network or checksum errors, wait a bit and retry.
        while True:
            progress = 0

            LOG.info("Starting download from %s to %s", image_url, destination_path)
            try:
                downloaded_sha256 = http.stream_to_file(
                    source_url=image_url,
                    destination_path=destination_path,
                    chunk_handler=handler,
                ).lower()
            except (
                *RETRIABLE_DOWNLOAD_ERRORS,
                http.DownloadMismatchError,
                http.DownloadDecompressError,
            ):
                # Use random timeout to avoid thundering herd
                timeout = random.randint(5, 60)
                LOG.exception("Download failed, retrying in %d seconds...", timeout)
                time.sleep(timeout)
                continue

            if expected_sha256 is not None and downloaded_sha256 != expected_sha256:
                LOG.warning(
                    "SHA256 mismatch, retry download. Expected: %s, got: %s",
                    expected_sha256,
                    downloaded_sha256,
                )
                # Use random timeout to avoid thundering herd
                timeout = random.randint(5, 60)
                LOG.warning("Download failed, retrying in %d seconds...", timeout)
                time.sleep(timeout)
                continue

            break

        # Finish the progress line with a newline so subsequent logs start fresh
        sys.stdout.write("\n")
        sys.stdout.flush()

    @property
    def _private_key_path(self) -> str:
        return os.path.join(
            c.ROOTFS_MOUNT_PATH,
            "var",
            "lib",
            "exordos",
            "universal_agent",
            "private_key",
        )

    def _empty_machine_resource(self) -> models.Resource:
        machine = models.GuestMachine(
            uuid=utils.system_uuid(),
            image="",
            status=c.MachineStatus.NEW.value,
        )

        return models.Resource(
            uuid=utils.system_uuid(),
            kind=KIND,
            status=c.MachineStatus.NEW.value,
            value=machine.dump_to_simple_view(),
            # Make fake hash
            hash=self._gen_hash(),
            full_hash=self._gen_hash(),
        )

    def run(self, api: CoreClientType, payload: models.Payload):
        """Flash the guest machine with the image from the payload."""
        if self._is_ready():
            return

        # Create a machine resource if it doesn't exist
        if not self._machine:
            empty_machine = self._empty_machine_resource()
            try:
                self._machine = api.resources_create(empty_machine)
            except core.ResourceAlreadyExists:
                # It normal if the resource already exists
                pass

        # Build the machine from paload
        try:
            guest = payload.capabilities[KIND]["resources"][0]["value"]
        except (KeyError, IndexError):
            LOG.warning("No guest machine found, skipping")
            return

        LOG.warning("Guest machine: %s", guest)
        self._machine = models.GuestMachine.restore_from_simple_view(**guest)

        # Check boot method
        if self._machine.boot.startswith("hd"):
            LOG.warning("Boot from HD required, rebooting")
            self._reboot()

        if not self._machine.image.startswith("http"):
            raise ValueError(f"Image is not a URL: {self._machine.image}")

        LOG.warning("Flashing progress: 0%")

        # Set the status to IN_PROGRESS in the Status API
        guest["status"] = c.MachineStatus.IN_PROGRESS.value
        api.resources_update(
            KIND,
            self._machine.uuid,
            status=c.MachineStatus.IN_PROGRESS.value,
            value=guest,
            full_hash=self._gen_hash(),
        )

        self._download_image(
            image_url=self._machine.image,
            destination_path=DEFAULT_BLOCK_DEVICE,
        )
        LOG.warning("Flashing progress: 100%")

        utils.flush_disk(DEFAULT_BLOCK_DEVICE)

        block_devices = utils.block_devices()
        try:
            utils.mount_root_partition(block_devices, mount_point=c.ROOTFS_MOUNT_PATH)

            # Get the secret key from the Boot API and prepare
            # it for the agents in the main OS.
            private_key = api.private_keys_refresh(self._machine.uuid)
            os.makedirs(os.path.dirname(self._private_key_path), exist_ok=True)
            with open(self._private_key_path, "w", opener=ro_opener) as f:
                f.write(private_key)
            LOG.warning("Private key written to %s", self._private_key_path)

            utils.unmount_root_partition(mount_point=c.ROOTFS_MOUNT_PATH)
            LOG.warning("Root partition unmounted")
        except utils.SupportedFSNotFound as e:
            LOG.warning("Supported FS not found, skip writing private key: %s", e)

        # Set the status to FLASHED in the Status API
        guest["status"] = c.MachineStatus.FLASHED.value
        api.resources_update(
            KIND,
            self._machine.uuid,
            status=c.MachineStatus.FLASHED.value,
            value=guest,
            full_hash=self._gen_hash(),
        )

        self._mark_ready()
        self._reboot()
