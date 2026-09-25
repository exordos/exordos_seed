#    Copyright 2026 Genesis Corporation.
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

import socket
import unittest
from unittest import mock
import urllib.error

try:
    import compression.zstd  # noqa: F401
except ImportError:
    # The http module needs compression.zstd, available since Python 3.14
    raise unittest.SkipTest("compression.zstd requires Python 3.14+")

from exordos_seed.common.http import base as http
from exordos_seed.drivers import guest

IMAGE_URL = "http://repo.example/image.raw"


class StreamTimeoutTest(unittest.TestCase):
    def setUp(self):
        # A server that accepts connections but never answers
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.addCleanup(self.server.close)
        port = self.server.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/image.raw"

    def test_stream_to_bytes_silent_server_times_out(self):
        with self.assertRaises(TimeoutError):
            http.stream_to_bytes(self.url, timeout=0.2)

    def test_stream_to_file_silent_server_times_out(self):
        with self.assertRaises(TimeoutError):
            http.stream_to_file(self.url, "/dev/null", timeout=0.2)


@mock.patch.object(guest.time, "sleep")
@mock.patch.object(guest.http, "stream_to_file", return_value="abc")
class DownloadImageRetryTest(unittest.TestCase):
    def _not_found(self):
        return urllib.error.HTTPError(IMAGE_URL, 404, "Not Found", {}, None)

    def test_download_image_checksum_timeout_retries(self, stream_to_file, sleep):
        with mock.patch.object(
            guest.http,
            "stream_to_bytes",
            side_effect=[TimeoutError(), b"abc  image.raw\n"],
        ) as stream_to_bytes:
            guest.GuestCapDriver()._download_image(IMAGE_URL, "/dev/null")

        self.assertEqual(stream_to_bytes.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        stream_to_file.assert_called_once()

    def test_download_image_read_timeout_retries(self, stream_to_file, sleep):
        stream_to_file.side_effect = [TimeoutError(), ConnectionResetError(), "abc"]
        with mock.patch.object(
            guest.http, "stream_to_bytes", side_effect=self._not_found()
        ):
            guest.GuestCapDriver()._download_image(IMAGE_URL, "/dev/null")

        self.assertEqual(stream_to_file.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
