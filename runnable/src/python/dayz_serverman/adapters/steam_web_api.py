"""Keyless Steam Web API lookup of Workshop item details over direct HTTPS."""

from __future__ import annotations

import http.client
import re
import ssl
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlencode

from ..domain.profiles import WORKSHOP_ID
from ..domain.update_check import (
    MAX_IDS_PER_REQUEST,
    RemoteBatchFailure,
    RemoteBatchResult,
    RemoteItem,
    RemoteItemResult,
)
from ..observability.structured_log import StructuredLogger
from .steam_web_details import MalformedResponse, classify_entries, parse_entries

# Fixed endpoint of the keyless published-file details call
API_HOST = "api.steampowered.com"
API_PORT = 443
API_PATH = "/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
# The only header fields the request sets besides the mandatory host and length
REQUEST_HEADERS = (
    ("Content-Type", "application/x-www-form-urlencoded"),
    ("Accept", "application/json"),
    ("User-Agent", "DayZ-ServerMan"),
)
# Transport limits: total wall-clock budget and response body cap
MAX_DEADLINE_SECONDS = 10.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
# Decimal text form accepted for a declared body length
_DECIMAL_TEXT = re.compile(r"[0-9]{1,20}")


class ResponseStream(Protocol):
    """Status, declared length and bounded body reads of one HTTP answer."""

    status: int
    declared_length: int | None

    # Return at most limit body bytes; empty bytes mean the body ended
    def read(self, limit: int) -> bytes: ...

    # Release the connection of this answer
    def close(self) -> None: ...


class HttpsTransport(Protocol):
    """Send one HTTPS POST and return the answer before its body is read."""

    # remaining() returns the seconds left and raises TimeoutError when none are
    def post(
        self, host: str, port: int, path: str, headers: Sequence[tuple[str, str]],
        body: bytes, remaining: Callable[[], float],
    ) -> ResponseStream: ...


class _BatchFailed(Exception):
    """Carry one batch failure code out of the request steps."""

    def __init__(self, failure: RemoteBatchFailure) -> None:
        """Store the failure code that ends the batch."""
        super().__init__(failure.value)
        self.failure = failure


class _HttpResponseStream:
    """Body reader over one standard-library HTTPS answer."""

    def __init__(
        self, connection: http.client.HTTPSConnection, sock: Any,
        response: http.client.HTTPResponse, remaining: Callable[[], float],
    ) -> None:
        """Keep the open exchange and read the status and declared length."""
        self._connection = connection
        self._sock = sock
        self._response = response
        self._remaining = remaining
        self.status = response.status
        # Only a plain decimal length header counts as a declared length
        declared = response.getheader("Content-Length")
        self.declared_length = (
            int(declared) if declared is not None and _DECIMAL_TEXT.fullmatch(declared) else None
        )

    def read(self, limit: int) -> bytes:
        """Return the next body bytes within the remaining time."""
        # Shrink the socket timeout to what is left of the deadline
        self._sock.settimeout(self._remaining())
        return self._response.read1(limit)

    def close(self) -> None:
        """Close the answer and its connection."""
        self._response.close()
        self._connection.close()


class DirectHttpsTransport:
    """Open one verified HTTPS exchange without proxies or redirects."""

    def post(
        self, host: str, port: int, path: str, headers: Sequence[tuple[str, str]],
        body: bytes, remaining: Callable[[], float],
    ) -> ResponseStream:
        """Connect directly, send the request and return the unread answer."""
        # Verify the certificate chain and the host name against the system trust store
        context = ssl.create_default_context()
        # http.client reads no environment proxy settings and follows no redirect
        connection = http.client.HTTPSConnection(
            host, port, timeout=remaining(), context=context,
        )
        try:
            # Limitation: the name lookup inside connect() is not bound by the deadline
            connection.connect()
            sock = connection.sock
            # Send only the fixed header set plus the mandatory host and length
            sock.settimeout(remaining())
            connection.putrequest("POST", path, skip_accept_encoding=True)
            for name, value in headers:
                connection.putheader(name, value)
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            # Wait for the status line and headers within the remaining time
            sock.settimeout(remaining())
            response = connection.getresponse()
        except BaseException:
            # Release the socket when any stage fails
            connection.close()
            raise
        return _HttpResponseStream(connection, sock, response, remaining)


class SteamWebApiCatalog:
    """Fetch remote update time and size for a bounded Workshop id set."""

    def __init__(
        self, *, transport: HttpsTransport | None = None,
        logger: StructuredLogger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the transport, the optional logger and the monotonic clock."""
        self._transport = transport if transport is not None else DirectHttpsTransport()
        self._logger = logger
        self._clock = clock

    def fetch(self, ids: Sequence[str], deadline_seconds: float) -> RemoteBatchResult:
        """Send one request and return item results or one failure code; never raises."""
        started = self._clock()
        # Status and byte count of the exchange, kept for the response event
        evidence: dict[str, int | None] = {"status": None, "bytes": 0}
        try:
            result = self._fetch(ids, deadline_seconds, started, evidence)
        except _BatchFailed as error:
            result = RemoteBatchResult(failure=error.failure)
        except MalformedResponse:
            result = RemoteBatchResult(failure=RemoteBatchFailure.RESPONSE_MALFORMED)
        except Exception:
            # Any unforeseen error ends the batch without reaching the caller
            result = RemoteBatchResult(failure=RemoteBatchFailure.INTERNAL)
        # Record the outcome without body, headers or ids
        self._log("update_check.response", {
            "outcome": "OK" if result.ok else "FAILED",
            "code": result.failure.value if result.failure else None,
            "status": evidence["status"],
            "byte_count": evidence["bytes"],
            "duration_ms": int((self._clock() - started) * 1000),
        }, level="INFO" if result.ok else "WARNING")
        return result

    def _fetch(
        self, ids: Sequence[str], deadline_seconds: float, started: float,
        evidence: dict[str, int | None],
    ) -> RemoteBatchResult:
        """Validate the ids, run the exchange and classify every requested id."""
        # Refuse an id set outside the request limits without sending anything
        requested = tuple(ids)
        if not 1 <= len(requested) <= MAX_IDS_PER_REQUEST:
            raise _BatchFailed(RemoteBatchFailure.INTERNAL)
        # Send each well-formed id once; an invalid id never leaves the process
        valid = tuple(dict.fromkeys(
            item for item in requested
            if isinstance(item, str) and WORKSHOP_ID.fullmatch(item) is not None
        ))
        self._log("update_check.request", {"id_count": len(valid)})
        answered: dict[str, RemoteItem] = {}
        if valid:
            body = self._exchange(valid, deadline_seconds, started, evidence)
            answered = classify_entries(valid, parse_entries(body))
        # Report one result per distinct requested id, in request order
        invalid = RemoteItemResult.INVALID
        return RemoteBatchResult(items=tuple(
            answered.get(key, RemoteItem(key, invalid))
            for key in dict.fromkeys(str(item) for item in requested)
        ))

    def _exchange(
        self, valid: tuple[str, ...], deadline_seconds: float, started: float,
        evidence: dict[str, int | None],
    ) -> bytes:
        """Post the form within the deadline and return the capped body."""
        expires = started + min(float(deadline_seconds), MAX_DEADLINE_SECONDS)

        def remaining() -> float:
            """Return the seconds left; raise when the deadline has passed."""
            left = expires - self._clock()
            if not left > 0:
                raise TimeoutError("update check deadline passed")
            return left

        try:
            stream = self._transport.post(
                API_HOST, API_PORT, API_PATH, REQUEST_HEADERS, _form_body(valid), remaining,
            )
            try:
                # Accept only status 200; a redirect is a failure, never followed
                evidence["status"] = stream.status
                if stream.status != 200:
                    raise _BatchFailed(RemoteBatchFailure.HTTP_STATUS)
                return _read_body(stream, remaining, evidence)
            finally:
                _close_quietly(stream)
        except TimeoutError as error:
            raise _BatchFailed(RemoteBatchFailure.TIMEOUT) from error
        except ssl.SSLError as error:
            raise _BatchFailed(RemoteBatchFailure.TLS_FAILURE) from error
        except OSError as error:
            # Name resolution, refused, unreachable and reset connections
            raise _BatchFailed(RemoteBatchFailure.NETWORK_UNREACHABLE) from error

    def _log(self, event: str, fields: Mapping[str, Any], *, level: str = "INFO") -> None:
        """Emit one structured event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(event, level=level, fields=fields)
        except (OSError, TypeError, ValueError):
            return


def _form_body(valid: tuple[str, ...]) -> bytes:
    """Return the form-encoded item count and indexed id fields."""
    pairs = [("itemcount", str(len(valid)))]
    pairs.extend((f"publishedfileids[{index}]", key) for index, key in enumerate(valid))
    return urlencode(pairs).encode("ascii")


def _read_body(
    stream: ResponseStream, remaining: Callable[[], float], evidence: dict[str, int | None],
) -> bytes:
    """Read the body up to the cap; refuse a declared or actual oversize."""
    # Refuse a declared length above the cap before reading
    declared = stream.declared_length
    if declared is not None and declared > MAX_RESPONSE_BYTES:
        raise _BatchFailed(RemoteBatchFailure.RESPONSE_TOO_LARGE)
    chunks: list[bytes] = []
    total = 0
    # Stop reading at one byte above the cap
    while total <= MAX_RESPONSE_BYTES:
        remaining()
        chunk = stream.read(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        evidence["bytes"] = total
    if total > MAX_RESPONSE_BYTES:
        raise _BatchFailed(RemoteBatchFailure.RESPONSE_TOO_LARGE)
    return b"".join(chunks)


def _close_quietly(stream: ResponseStream) -> None:
    """Close the answer; a close error must not change the batch result."""
    try:
        stream.close()
    except Exception:
        return
