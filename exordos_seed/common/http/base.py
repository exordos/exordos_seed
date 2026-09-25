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
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import typing as tp
import urllib.error
import urllib.parse
import urllib.request
import zlib

import compression.zstd as zstd

from exordos_seed.common import constants as c
from exordos_seed.common import exceptions

LOG = logging.getLogger(__name__)

HttpMethodType = tp.Literal["GET", "POST", "PUT", "DELETE"]
JSON_CONTENT_TYPE = "application/json"


class HttpError(exceptions.GSException):
    message = "Http error: %(http_err)s"


class HttpNotFoundError(exceptions.GSException):
    message = "Http not found error: %(http_err)s"


class HttpConflictError(exceptions.GSException):
    message = "Http conflict error: %(http_err)s"


class DownloadMismatchError(exceptions.GSException):
    message = "Downloaded mismatch. Expected: %(expected)s, got: %(got)s"


class DownloadDecompressError(exceptions.GSException):
    message = (
        "EOF not found while decompressing, broken original archive or download error."
    )


class HttpResp:
    def __init__(self, resp_code: int, text: bytes, content_type: str) -> None:
        self.text = text
        self.content_type = content_type
        self.resp_code = resp_code

    def json(self) -> dict[str, tp.Any] | None:
        if self.content_type == JSON_CONTENT_TYPE:
            return json.loads(self.text.decode("utf-8"))

        raise HttpError(http_err="Invalid content type")

    def raise_for_status(self):
        if self.resp_code < 400:
            return

        if self.resp_code == 404:
            raise HttpNotFoundError(http_err=self.text)
        elif self.resp_code == 409:
            raise HttpConflictError(http_err=self.text)

        raise HttpError(http_err=self.text)


class HttpClient:
    def __init__(self, timeout: int = 20):
        self._timeout = timeout

    def _request(
        self,
        method: HttpMethodType,
        url: str,
        data: tp.Any | None = None,
        headers: dict[str, tp.Any] | None = None,
        params: dict[str, tp.Any] | None = None,
    ) -> HttpResp:
        if headers is None:
            headers = {}

        if params is not None:
            url += "?" + urllib.parse.urlencode(params)

        if data is not None:
            if isinstance(data, dict) or isinstance(data, list):
                data = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = JSON_CONTENT_TYPE

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                return HttpResp(
                    resp_code=response.status,
                    text=response.read(),
                    content_type=response.getheader("Content-Type"),
                )
        except urllib.error.HTTPError as e:
            return HttpResp(
                resp_code=e.code,
                text=e.read().decode("utf-8"),
                content_type=e.headers.get("Content-Type", ""),
            )

    def get(
        self,
        url: str,
        params: dict[str, tp.Any] | None = None,
        headers: dict[str, tp.Any] | None = None,
        raise_for_status: bool = True,
    ) -> HttpResp:
        if params:
            url += "?" + urllib.parse.urlencode(params)
        resp = self._request("GET", url, headers=headers)
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def post(
        self,
        url: str,
        data: str | dict[str, tp.Any],
        headers: dict[str, tp.Any] | None = None,
        raise_for_status: bool = True,
    ) -> HttpResp:
        resp = self._request("POST", url, data=data, headers=headers)
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def put(
        self,
        url: str,
        data: str | dict[str, tp.Any],
        headers: dict[str, tp.Any] | None = None,
        raise_for_status: bool = True,
    ) -> HttpResp:
        resp = self._request("PUT", url, data=data, headers=headers)
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def delete(
        self,
        url: str,
        headers: dict[str, tp.Any] | None = None,
        raise_for_status: bool = True,
    ) -> HttpResp:
        resp = self._request("DELETE", url, headers=headers)
        if raise_for_status:
            resp.raise_for_status()
        return resp


class BaseChunkHandler:
    def __init__(self, content_length: int) -> None:
        self.in_bytes = 0
        self.out_bytes = 0

        self.content_length = content_length

    def handle_chunk(self, chunk: bytes, out_file: tp.BinaryIO) -> int:
        return 0

    def is_clean(self):
        if not self.content_length == self.in_bytes:
            raise DownloadMismatchError(expected=self.content_length, got=self.in_bytes)


class PlainChunkHandler(BaseChunkHandler):
    def handle_chunk(self, chunk: bytes, out_file: tp.BinaryIO) -> int:
        out_file.write(chunk)
        written = len(chunk)
        self.in_bytes += written
        self.out_bytes += written
        return written


class GZChunkHandler(BaseChunkHandler):
    def __init__(self, content_length: int, chunk_size: int):
        self.chunk_size = chunk_size
        # Set appropriate wbits to support gzip
        self.d = zlib.decompressobj(wbits=15 + 32)
        super().__init__(content_length=content_length)

    def handle_chunk(self, chunk: bytes, out_file: tp.BinaryIO) -> int:
        self.in_bytes += len(chunk)
        tail = chunk
        written = 0
        while True:
            # there may be many zeroes, so decompress in chunks
            raw_chunk = self.d.decompress(tail, max_length=self.chunk_size)
            if not raw_chunk:
                break
            tail = self.d.unconsumed_tail
            out_file.write(raw_chunk)
            written += len(raw_chunk)

        self.out_bytes += written
        return written

    def is_clean(self):
        if not self.d.eof:
            raise DownloadDecompressError()
        super().is_clean()


class ZstdChunkHandler(BaseChunkHandler):
    def __init__(self, content_length: int, chunk_size: int):
        self.chunk_size = chunk_size
        self.d = zstd.ZstdDecompressor()
        super().__init__(content_length=content_length)

    def handle_chunk(self, chunk: bytes, out_file: tp.BinaryIO) -> int:
        if not chunk:
            return 0
        self.in_bytes += len(chunk)
        tail = chunk
        written = 0
        while True:
            raw_chunk = self.d.decompress(tail, max_length=self.chunk_size)
            if raw_chunk:
                out_file.write(raw_chunk)
                written += len(raw_chunk)
            if self.d.needs_input or self.d.eof:
                break
            tail = b""
        self.out_bytes += written
        return written

    def is_clean(self):
        if not self.d.eof:
            raise DownloadDecompressError()
        super().is_clean()


def stream_to_file(
    source_url: str,
    destination_path: str,
    chunk_size: int = c.CHUNK_SIZE,
    chunk_handler: tp.Callable | None = None,
    timeout: float = c.DOWNLOAD_TIMEOUT,
) -> str:
    """
    Downloads a file from a source URL and streams it to a destination path.

    The caller can pass a chunk handler that will be called for each chunk of
    data as it is received. The handler is passed the total content length of
    the file, the amount of data written to disk, and the actual chunk of data.

    :param source_url: URL to download from
    :param destination_path: Path to write the file to
    :param chunk_size: Size of chunks to read from the URL in bytes
    :param chunk_handler: Optional callable to call for each chunk of data
    :param timeout: Socket timeout in seconds for connect and each read
    :raises DownloadMismatchError: If the total amount of data written to disk
        does not match the content length in the HTTP response headers
    :returns: Hex-encoded SHA-256 checksum of the downloaded (compressed)
        byte stream as it was received from the server
    """
    read = written = 0
    sha256 = hashlib.sha256()

    req = urllib.request.Request(source_url)
    # It's absurd to compress already compressed file
    if not source_url.endswith(".gz") and not source_url.endswith(".zst"):
        req.add_header("Accept-Encoding", "zstd, gzip")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_length = int(response.headers.get("Content-Length", 0))
        is_gzipped = response.headers.get(
            "Content-Encoding"
        ) == "gzip" or source_url.endswith(".gz")
        is_zstd = response.headers.get(
            "Content-Encoding"
        ) == "zstd" or source_url.endswith(".zst")

        if is_zstd:
            LOG.warning("Got zstd stream/file, progress will be inaccurate...")
            chunker = ZstdChunkHandler(content_length, chunk_size=chunk_size)
        elif is_gzipped:
            LOG.warning("Got gzipped stream/file, progress will be inaccurate...")
            chunker = GZChunkHandler(content_length, chunk_size=chunk_size)
        else:
            chunker = PlainChunkHandler(content_length)

        with open(destination_path, "wb") as file:
            chunks_since_sync = 0
            while True:
                chunk = response.read(chunk_size)
                read += len(chunk)
                if chunk:
                    sha256.update(chunk)
                written += chunker.handle_chunk(chunk, file)

                if not chunk:
                    break

                # Periodically flush page cache to disk to avoid accumulating
                # gigabytes of dirty pages that cause a large blocking fsync
                # at the end, which is critical on low-memory VMs.
                chunks_since_sync += 1
                if chunks_since_sync >= 64:
                    file.flush()
                    os.fsync(file.fileno())
                    chunks_since_sync = 0

                if chunk_handler is not None:
                    chunk_handler(content_length, read, written, chunk)

            # Flush and sync to ensure data is written to disk
            file.flush()
            os.fsync(file.fileno())

    chunker.is_clean()
    return sha256.hexdigest()


def stream_to_bytes(
    source_url: str,
    chunk_size: int = c.CHUNK_SIZE,
    chunk_handler: tp.Callable | None = None,
    timeout: float = c.DOWNLOAD_TIMEOUT,
) -> bytes:
    """
    Downloads a file from a source URL and returns it as bytes.

    The caller can pass a chunk handler that will be called for each chunk of
    data as it is received. The handler is passed the total content length of
    the file, the amount of data written to disk, and the actual chunk of data.

    :param source_url: URL to download from
    :param chunk_size: Size of chunks to read from the URL in bytes
    :param chunk_handler: Optional callable to call for each chunk of data
    :param timeout: Socket timeout in seconds for connect and each read
    :returns: The downloaded data as bytes
    """
    read = written = 0

    req = urllib.request.Request(source_url)
    if not source_url.endswith(".gz") and not source_url.endswith(".zst"):
        req.add_header("Accept-Encoding", "zstd, gzip")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_length = int(response.headers.get("Content-Length", 0))
        is_gzipped = response.headers.get(
            "Content-Encoding"
        ) == "gzip" or source_url.endswith(".gz")
        is_zstd = response.headers.get(
            "Content-Encoding"
        ) == "zstd" or source_url.endswith(".zst")

        if is_zstd:
            LOG.warning("Got zstd stream/file, progress will be inaccurate...")
            chunker = ZstdChunkHandler(content_length, chunk_size=chunk_size)
        elif is_gzipped:
            LOG.warning("Got gzipped stream/file, progress will be inaccurate...")
            chunker = GZChunkHandler(content_length, chunk_size=chunk_size)
        else:
            chunker = PlainChunkHandler(content_length)

        out = io.BytesIO()
        while True:
            chunk = response.read(chunk_size)
            read += len(chunk)
            written += chunker.handle_chunk(chunk, out)

            if not chunk:
                break

            if chunk_handler is not None:
                chunk_handler(content_length, read, written, chunk)

    chunker.is_clean()
    return out.getvalue()
