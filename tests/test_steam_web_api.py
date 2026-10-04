"""Steam Web API adapter: request form, limits, failure codes and item results."""
from __future__ import annotations

import json
import socket
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters import steam_web_api as api  # noqa: E402
from dayz_serverman.domain.update_check import (  # noqa: E402
    RemoteBatchFailure as Failure,
    RemoteItem,
    RemoteItemResult as Result,
)
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402


def entry(workshop_id: object, **changes: object) -> dict[str, object]:
    """Return one well-formed details entry with optional field changes."""
    base = {
        "publishedfileid": workshop_id, "result": 1, "consumer_app_id": 221100,
        "time_updated": 1_700_000_000, "file_size": "4096",
        "title": "SECRET-TITLE", "description": "SECRET-DESCRIPTION",
    }
    return {key: value for key, value in {**base, **changes}.items() if value is not None}


def details(*entries: object) -> bytes:
    """Return the JSON body that carries the given details entries."""
    return json.dumps({"response": {"publishedfiledetails": list(entries)}}).encode("utf-8")


class FakeStream:
    """In-memory answer that records how many bytes were read."""

    def __init__(self, body: bytes = b"", status: int = 200, declared: object = "body") -> None:
        """Store the body, the status and the declared length."""
        self.status = status
        self.declared_length = len(body) if declared == "body" else declared
        self._body = body
        self.served = 0
        self.closed = False
        self.on_read = lambda: None

    def read(self, limit: int) -> bytes:
        """Return the next slice of the body, at most limit bytes."""
        self.on_read()
        chunk = self._body[self.served:self.served + limit]
        self.served += len(chunk)
        return chunk

    def close(self) -> None:
        """Record that the adapter released the answer."""
        self.closed = True


class FakeTransport:
    """Transport double that never opens a socket."""

    def __init__(self, stream: FakeStream | None = None, error: Exception | None = None) -> None:
        """Store the answer to return or the error to raise."""
        self.stream = stream
        self.error = error
        self.calls: list[tuple] = []

    def post(self, host, port, path, headers, body, remaining):
        """Record the request and return the prepared answer."""
        self.calls.append((host, port, path, tuple(headers), body, remaining()))
        if self.error is not None:
            raise self.error
        return self.stream


class SteamWebApiCatalogTests(unittest.TestCase):
    """Transport rules of the remote check, proven without a network connection."""

    def setUp(self) -> None:
        """Freeze the clock, prepare a log file and forbid real connections."""
        self.temporary = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temporary.name) / "manager.jsonl"
        self.now = 100.0
        guard = patch("socket.create_connection", side_effect=AssertionError("no network"))
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.temporary.cleanup)

    def fetch(self, transport: FakeTransport, ids: object, deadline: float = 10.0):
        """Run one fetch against the fake transport with the test clock and log."""
        catalog = api.SteamWebApiCatalog(
            transport=transport, logger=StructuredLogger(self.log_path), clock=lambda: self.now,
        )
        return catalog.fetch(ids, deadline)

    def test_request_form_for_one_and_two_hundred_ids(self) -> None:
        """The request goes to the fixed endpoint with only the specified fields."""
        for count in (1, 200):
            with self.subTest(count=count):
                ids = [str(1000 + index) for index in range(count)]
                transport = FakeTransport(FakeStream(details(*(entry(key) for key in ids))))
                result = self.fetch(transport, ids, deadline=60.0)
                self.assertTrue(result.ok)
                self.assertEqual([item.workshop_id for item in result.items], ids)
                host, port, path, headers, body, budget = transport.calls[0]
                self.assertEqual((host, port), ("api.steampowered.com", 443))
                self.assertEqual(path, "/ISteamRemoteStorage/GetPublishedFileDetails/v1/")
                self.assertEqual(headers, (
                    ("Content-Type", "application/x-www-form-urlencoded"),
                    ("Accept", "application/json"),
                    ("User-Agent", "DayZ-ServerMan"),
                ))
                expected = [("itemcount", str(count))]
                expected += [(f"publishedfileids[{index}]", key) for index, key in enumerate(ids)]
                self.assertEqual(parse_qsl(body.decode("ascii"), strict_parsing=True), expected)
                # A longer caller deadline is capped at the 10 s transport limit
                self.assertEqual(budget, 10.0)
                self.assertTrue(transport.stream.closed)

    def test_zero_and_two_hundred_one_ids_are_refused_without_a_request(self) -> None:
        """An id set outside 1 to 200 sends nothing and yields no item results."""
        for ids in ([], [str(1000 + index) for index in range(201)]):
            with self.subTest(count=len(ids)):
                transport = FakeTransport(FakeStream(details()))
                result = self.fetch(transport, ids)
                self.assertEqual((result.failure, result.items), (Failure.INTERNAL, ()))
                self.assertEqual(transport.calls, [])

    def test_invalid_ids_are_never_sent(self) -> None:
        """A malformed id gets INVALID and stays out of the request body."""
        transport = FakeTransport(FakeStream(details(entry("111"))))
        result = self.fetch(transport, ["111", "0", "12x", "111", "1" * 21])
        self.assertEqual(result.items, (
            RemoteItem("111", Result.OK, 1_700_000_000, 4096), RemoteItem("0", Result.INVALID),
            RemoteItem("12x", Result.INVALID), RemoteItem("1" * 21, Result.INVALID),
        ))
        self.assertEqual(transport.calls[0][4], b"itemcount=1&publishedfileids%5B0%5D=111")
        # A set with no valid id sends nothing and still answers every id
        silent = FakeTransport()
        self.assertEqual(self.fetch(silent, ["abc"]).items, (RemoteItem("abc", Result.INVALID),))
        self.assertEqual(silent.calls, [])

    def test_transport_errors_map_to_failure_codes(self) -> None:
        """Every connection-stage error becomes its batch failure code."""
        cases = (
            (TimeoutError("timed out"), Failure.TIMEOUT),
            (ssl.SSLCertVerificationError("bad certificate"), Failure.TLS_FAILURE),
            (ssl.SSLError("handshake failure"), Failure.TLS_FAILURE),
            (socket.gaierror("name resolution"), Failure.NETWORK_UNREACHABLE),
            (ConnectionRefusedError("refused"), Failure.NETWORK_UNREACHABLE),
            (ConnectionResetError("reset"), Failure.NETWORK_UNREACHABLE),
            (OSError("unreachable"), Failure.NETWORK_UNREACHABLE),
            (RuntimeError("unexpected"), Failure.INTERNAL),
        )
        for error, code in cases:
            with self.subTest(error=type(error).__name__):
                result = self.fetch(FakeTransport(error=error), ["111"])
                self.assertEqual((result.failure, result.items), (code, ()))

    def test_deadline_is_enforced_before_and_during_the_read(self) -> None:
        """A passed deadline gives TIMEOUT at every stage."""
        # No time left before the request is sent
        expired = FakeTransport(FakeStream(details(entry("111"))))
        self.assertEqual(self.fetch(expired, ["111"], deadline=0).failure, Failure.TIMEOUT)
        # The clock passes the deadline while the body is read
        stream = FakeStream(details(entry("111")))
        stream.on_read = lambda: setattr(self, "now", self.now + 3.5)
        result = self.fetch(FakeTransport(stream), ["111"], deadline=3.0)
        self.assertEqual(result.failure, Failure.TIMEOUT)
        self.assertTrue(stream.closed)

    def test_only_status_200_is_accepted(self) -> None:
        """A redirect or an error status fails the batch and reads no body."""
        for status in (302, 404, 500):
            with self.subTest(status=status):
                stream = FakeStream(details(entry("111")), status=status)
                result = self.fetch(FakeTransport(stream), ["111"])
                self.assertEqual((result.failure, stream.served), (Failure.HTTP_STATUS, 0))

    def test_declared_and_actual_oversize_are_refused(self) -> None:
        """A declared length above the cap is not read; reading stops at cap + 1."""
        cap, too_large = api.MAX_RESPONSE_BYTES, Failure.RESPONSE_TOO_LARGE
        declared = FakeStream(b"{}", declared=cap + 1)
        self.assertEqual(self.fetch(FakeTransport(declared), ["111"]).failure, too_large)
        self.assertEqual(declared.served, 0)
        actual = FakeStream(b" " * (cap + 4096), declared=None)
        self.assertEqual(self.fetch(FakeTransport(actual), ["111"]).failure, too_large)
        self.assertEqual(actual.served, cap + 1)
        # A body of exactly the cap is accepted
        padded = details(entry("111"))
        exact = FakeStream(padded + b" " * (cap - len(padded)), declared=None)
        self.assertTrue(self.fetch(FakeTransport(exact), ["111"]).ok)

    def test_malformed_bodies_are_refused(self) -> None:
        """Bad UTF-8, bad JSON, NaN, Infinity and a wrong shape fail the batch."""
        bodies = {
            "bad utf-8": b'{"response": {"publishedfiledetails": ["\xff"]}}',
            "bad json": b'{"response": ',
            "nan": b'{"response": {"publishedfiledetails": [], "x": NaN}}',
            "infinity": b'{"response": {"publishedfiledetails": [], "x": -Infinity}}',
            "list root": b"[]",
            "no response": b"{}",
            "response not object": b'{"response": []}',
            "details not list": b'{"response": {"publishedfiledetails": {}}}',
            "empty": b"",
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                result = self.fetch(FakeTransport(FakeStream(body)), ["111"])
                self.assertEqual((result.failure, result.items), (Failure.RESPONSE_MALFORMED, ()))

    def test_item_result_table(self) -> None:
        """Each row of the item-result table yields its result; the first match wins."""
        body = details(
            entry("999"), "not-an-object", entry(None),
            entry("101"), entry("101"),
            entry("103", result=9), entry("104", result=True),
            entry("105", consumer_app_id=107410), entry("106", consumer_app_id="221100"),
            entry("107", time_updated=None), entry("108", time_updated=0),
            entry("109", time_updated="1700000000"), entry("110", time_updated=True),
            entry(111, file_size=2048), entry("112", file_size=None),
            entry("113", file_size="12a"), entry("114", file_size=-1),
            entry("115", result=9, consumer_app_id=1, time_updated=None),
        )
        ids = [str(value) for value in range(101, 116)]
        result = self.fetch(FakeTransport(FakeStream(body)), ids)
        invalid, time = Result.INVALID, 1_700_000_000
        self.assertEqual(result.items, (
            RemoteItem("101", invalid), RemoteItem("102", invalid),
            RemoteItem("103", Result.NOT_FOUND), RemoteItem("104", Result.NOT_FOUND),
            RemoteItem("105", Result.WRONG_APP), RemoteItem("106", Result.WRONG_APP),
            RemoteItem("107", invalid), RemoteItem("108", invalid),
            RemoteItem("109", invalid), RemoteItem("110", invalid),
            RemoteItem("111", Result.OK, time, 2048), RemoteItem("112", Result.OK, time, None),
            RemoteItem("113", Result.OK, time, None), RemoteItem("114", Result.OK, time, None),
            RemoteItem("115", Result.NOT_FOUND),
        ))

    def test_text_fields_are_ignored_and_the_log_has_no_body(self) -> None:
        """Only the accepted fields leave the adapter; the log carries counts only."""
        transport = FakeTransport(FakeStream(details(entry("1559212036"))))
        result = self.fetch(transport, ["1559212036"])
        self.assertEqual(result.items, (RemoteItem("1559212036", Result.OK, 1_700_000_000, 4096),))
        self.fetch(FakeTransport(FakeStream(b"SECRET-BODY", status=500)), ["1559212036"])
        text = self.log_path.read_text(encoding="utf-8")
        for forbidden in ("1559212036", "SECRET", "publishedfile", "User-Agent", "application/"):
            self.assertNotIn(forbidden, text)
        records = [json.loads(line) for line in text.splitlines()]
        events = [record["event"] for record in records]
        self.assertEqual(events, ["update_check.request", "update_check.response"] * 2)
        self.assertEqual(records[0]["fields"], {"id_count": 1})
        self.assertEqual(records[1]["fields"], {
            "outcome": "OK", "code": None, "status": 200,
            "byte_count": len(details(entry("1559212036"))), "duration_ms": 0,
        })
        self.assertEqual(records[3]["fields"], {
            "outcome": "FAILED", "code": "HTTP_STATUS", "status": 500,
            "byte_count": 0, "duration_ms": 0,
        })


class DirectHttpsTransportTests(unittest.TestCase):
    """Connection rules of the real transport, proven with a fake connection class."""

    def test_connection_is_direct_verified_and_sends_only_the_fixed_headers(self) -> None:
        """Proxy variables are ignored, the certificate is verified, no extra header is set."""
        proxies = {"HTTPS_PROXY": "http://proxy.invalid:3128", "ALL_PROXY": "http://proxy.invalid"}
        connection_class = patch.object(api.http.client, "HTTPSConnection")
        with patch.dict("os.environ", proxies), connection_class as factory:
            connection = factory.return_value
            connection.getresponse.return_value.status = 302
            connection.getresponse.return_value.getheader.return_value = "12"
            stream = api.DirectHttpsTransport().post(
                api.API_HOST, api.API_PORT, api.API_PATH, api.REQUEST_HEADERS, b"abc", lambda: 7.0,
            )
        # One connection goes straight to the fixed host with a verifying context
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args, ("api.steampowered.com", 443))
        self.assertEqual(factory.call_args.kwargs["timeout"], 7.0)
        context = factory.call_args.kwargs["context"]
        self.assertEqual((context.verify_mode, context.check_hostname), (ssl.CERT_REQUIRED, True))
        request = connection.putrequest
        request.assert_called_once_with("POST", api.API_PATH, skip_accept_encoding=True)
        sent = [call.args for call in connection.putheader.call_args_list]
        self.assertEqual(sent, [*api.REQUEST_HEADERS, ("Content-Length", "3")])
        connection.endheaders.assert_called_once_with(b"abc")
        # The redirect answer is returned as it is; nothing follows it
        self.assertEqual((stream.status, stream.declared_length), (302, 12))
        connection.getresponse.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
