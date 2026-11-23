#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

import gzip
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

try:
    from compression import zstd
except ImportError:
    raise unittest.SkipTest("compression.zstd requires Python 3.14+")

from exordos_seed.common.http import base as http


class ImageWritebackTest(unittest.TestCase):
    def test_sync_bounds_uncompressed_writes_inside_one_response_chunk(self):
        interval = 16 << 10
        data = b"\0" * (interval * 6 + 123)
        for extension, encode in (
            ("raw", bytes),
            ("gz", gzip.compress),
            ("zst", zstd.compress),
        ):
            with (
                self.subTest(extension=extension),
                tempfile.TemporaryDirectory() as root,
            ):
                payload = encode(data)
                response = io.BytesIO(payload)
                response.headers = {"Content-Length": str(len(payload))}
                destination = Path(root) / "image"
                synchronized_sizes = []
                progress = []

                def sync(fd, sizes=synchronized_sizes):
                    sizes.append(os.fstat(fd).st_size)

                def report(*args, samples=progress, sizes=synchronized_sizes):
                    samples.append((args[2], len(sizes)))

                with (
                    mock.patch.object(
                        http.urllib.request, "urlopen", return_value=response
                    ),
                    mock.patch.object(http, "_SYNC_INTERVAL_BYTES", interval),
                    mock.patch.object(http.os, "fsync", side_effect=sync),
                ):
                    checksum = http.stream_to_file(
                        f"https://repo.example/image.{extension}",
                        str(destination),
                        chunk_size=len(data) * 2,
                        chunk_handler=report,
                    )

                self.assertEqual(destination.read_bytes(), data)
                self.assertEqual(checksum, hashlib.sha256(payload).hexdigest())
                self.assertEqual(
                    synchronized_sizes,
                    [interval * i for i in range(1, 7)] + [len(data)],
                )
                self.assertEqual(progress, [(len(data), 6)])

    def test_periodic_sync_failure_aborts_download(self):
        payload = zstd.compress(b"\0" * 100000)
        response = io.BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}
        progress = mock.Mock()
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(http.urllib.request, "urlopen", return_value=response),
            mock.patch.object(http, "_SYNC_INTERVAL_BYTES", 16384),
            mock.patch.object(
                http.os, "fsync", side_effect=OSError("disk sync failed")
            ),
            self.assertRaisesRegex(OSError, "disk sync failed"),
        ):
            http.stream_to_file(
                "https://repo.example/image.zst",
                str(Path(root) / "image"),
                chunk_handler=progress,
            )
        progress.assert_not_called()

    def test_short_images_still_receive_a_final_sync(self):
        for data in (b"", b"short image"):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as root:
                response = io.BytesIO(data)
                response.headers = {"Content-Length": str(len(data))}
                destination = Path(root) / "image"
                with (
                    mock.patch.object(
                        http.urllib.request, "urlopen", return_value=response
                    ),
                    mock.patch.object(http.os, "fsync") as sync,
                ):
                    http.stream_to_file(
                        "https://repo.example/image.raw", str(destination)
                    )
                self.assertEqual(destination.read_bytes(), data)
                sync.assert_called_once()

    def test_sync_interval_accumulates_across_input_chunks(self):
        data = b"X" * 67
        response = io.BytesIO(data)
        response.headers = {"Content-Length": str(len(data))}
        sizes = []
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(http.urllib.request, "urlopen", return_value=response),
            mock.patch.object(http, "_SYNC_INTERVAL_BYTES", 16),
            mock.patch.object(
                http.os,
                "fsync",
                side_effect=lambda fd: sizes.append(os.fstat(fd).st_size),
            ),
        ):
            destination = Path(root) / "image"
            http.stream_to_file(
                "https://repo.example/image.raw", str(destination), chunk_size=7
            )
            self.assertEqual(destination.read_bytes(), data)
        self.assertEqual(sizes, [16, 32, 48, 64, 67])
