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
import enum
import os
import uuid as sys_uuid

GLOBAL_SERVICE_NAME = "exordos_seed"
SERVICE_PROJECT_ID = sys_uuid.UUID("00000000-0000-0000-0000-000000000000")

WORK_DIR = "/var/lib/exordos"
NODE_UUID_PATH = os.path.join(WORK_DIR, "node-id")
PRIVATE_KEY_PATH = os.path.join(WORK_DIR, "private_key")
ROOTFS_MOUNT_PATH = "/mnt/"


def _chunk_size() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    size = (total_kb * 1024) // 16
                    return max(1 << 20, min(size, 16 << 20))
    except OSError:
        pass
    return 4 << 20


CHUNK_SIZE = _chunk_size()
# Socket timeout for image downloads, in seconds. It applies to connect and
# to each read separately, not to the whole download: a slow but steady
# transfer of a large image never hits it, only a stall with no data does.
DOWNLOAD_TIMEOUT = 60

KERNEL_CMDLINE_PATH = "/proc/cmdline"
GC_CMDLINE_DEF_PREFIX = "gc_"
GC_CMDLINE_KEY_BOOT_API = f"{GC_CMDLINE_DEF_PREFIX}boot_api"

AGENT_CAPABILITIES = ("guest_machine",)
AGENT_FACTS = ()
AGENT_PAYLOAD_PATH = "/payload.json"

# Autonomous mode constants
AUTONOMOUS_CMDLINE_KEY = "autonomous"
AUTONOMOUS_MOUNT_POINT = "/mnt/autonomous"
AUTONOMOUS_NETPLAN_DIR = f"{AUTONOMOUS_MOUNT_POINT}/netplan"
AUTONOMOUS_WORK_DIR = "/autonomous"
AUTONOMOUS_UPDATE_JSON_PATH = os.path.join(AUTONOMOUS_WORK_DIR, "update.json")
AUTONOMOUS_PRIVATE_KEY_PATH = os.path.join(AUTONOMOUS_WORK_DIR, "private_key")


class MachineStatus(str, enum.Enum):
    NEW = "NEW"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    STARTED = "STARTED"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    ERROR = "ERROR"
    FLASHED = "FLASHED"
