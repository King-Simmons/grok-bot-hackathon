"""JSON POST bodies must not hang or grow past the route cap.

A negative Content-Length used to make the handler read until the client
closed, so the client waited for a response that never started.
"""
import socket, time

from tests.server_case import ServerCase


def _status_line(raw):
    line = raw.split(b"\r\n", 1)[0]
    parts = line.split()
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
        return 0
    try:
        return int(parts[1])
    except ValueError:
        return 0


class ContentLengthTest(ServerCase):
    def raw_post(self, extra_headers, body=b"", timeout=1.5):
        head = [
            "POST /api/synthesize HTTP/1.1",
            f"Host: 127.0.0.1:{self.port}",
            "Content-Type: application/json",
            "Connection: close",
            *extra_headers,
            "",
            "",
        ]
        payload = "\r\n".join(head).encode() + body
        started = time.monotonic()
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        sock.settimeout(timeout)
        chunks = []
        try:
            sock.sendall(payload)
            while True:
                try:
                    block = sock.recv(65536)
                except socket.timeout:
                    break
                if not block:
                    break
                chunks.append(block)
        finally:
            sock.close()
        raw = b"".join(chunks)
        return _status_line(raw), raw, time.monotonic() - started

    def test_negative_content_length_is_rejected_quickly(self):
        status, raw, elapsed = self.raw_post(["Content-Length: -1"], b'{"url":"https://example.com"}')
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"bad content length", raw)

    def test_non_numeric_content_length_is_rejected(self):
        status, raw, elapsed = self.raw_post(["Content-Length: abc"], b'{"url":"https://example.com"}')
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"bad content length", raw)

    def test_huge_content_length_is_rejected(self):
        status, raw, elapsed = self.raw_post(
            ["Content-Length: 9999999999999999999"], b"{}",
        )
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 413, raw)
        self.assertIn(b"body too large", raw)

    def test_content_length_over_the_route_cap_is_rejected(self):
        # /api/synthesize accepts 10000 bytes. One byte past that is refused
        # before the handler tries to read the declared size.
        status, raw, elapsed = self.raw_post(["Content-Length: 10001"], b"{}")
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 413, raw)
        self.assertIn(b"body too large", raw)

    def test_missing_content_length_uses_bytes_already_sent(self):
        status, raw, elapsed = self.raw_post([], b"[]")
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"JSON object", raw)

    def test_missing_content_length_without_a_body_is_an_empty_object(self):
        status, raw, elapsed = self.raw_post([])
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"missing url", raw)

    def test_normal_json_post_still_reaches_the_route(self):
        status, _loc, _raw, body = self.post_json("/api/synthesize", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "missing url")

    def test_non_canonical_content_length_is_rejected(self):
        body = b'{"url":"x"}'
        for value in ("+10", "1_0", "-0"):
            status, raw, elapsed = self.raw_post([f"Content-Length: {value}"], body)
            self.assertLess(elapsed, 1.0, raw)
            self.assertEqual(status, 400, raw)
            self.assertIn(b"bad content length", raw, value)

    def test_disagreeing_content_lengths_are_rejected(self):
        status, raw, elapsed = self.raw_post(
            ["Content-Length: 2", "Content-Length: 50"], b"{}",
        )
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"bad content length", raw)

    def test_repeated_content_length_still_reaches_the_route(self):
        status, raw, elapsed = self.raw_post(
            ["Content-Length: 2", "Content-Length: 2"], b"{}",
        )
        self.assertLess(elapsed, 1.0, raw)
        self.assertEqual(status, 400, raw)
        self.assertIn(b"missing url", raw)

    def test_short_body_returns_408(self):
        status, raw, elapsed = self.raw_post(["Content-Length: 50"], b"{}", timeout=3)
        self.assertLess(elapsed, 2.5, raw)
        self.assertEqual(status, 408, raw)
        self.assertIn(b"request timeout", raw)

    def test_missing_length_restores_the_socket_timeout(self):
        left, right = socket.socketpair()
        right.settimeout(7)
        left.sendall(b"[]")
        handler = type("H", (), {})()
        handler.connection = right
        handler.rfile = right.makefile("rb")
        try:
            from engine.server import _read_arrived
            raw = _read_arrived(handler, 100)
            timeout = right.gettimeout()
        finally:
            handler.rfile.close()
            left.close()
            right.close()
        self.assertEqual(raw, b"[]")
        self.assertEqual(timeout, 7)

    def test_short_declared_body_uses_the_socket_timeout(self):
        left, right = socket.socketpair()
        right.settimeout(0.2)
        left.sendall(b"{}")
        handler = type("H", (), {})()
        handler.connection = right
        handler.rfile = right.makefile("rb")
        try:
            from engine.server import _read_exact
            started = time.monotonic()
            exact = _read_exact(handler, 50)
            elapsed = time.monotonic() - started
            timeout = right.gettimeout()
        finally:
            handler.rfile.close()
            left.close()
            right.close()
        self.assertIsNone(exact)
        self.assertGreater(elapsed, 0.15)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(timeout, 0.2)
