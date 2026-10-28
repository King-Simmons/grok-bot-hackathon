"""Fetch must not connect to a private, loopback, link-local, or metadata address.

The host check used to resolve a name, then the HTTP client resolved it again.
A second answer of 127.0.0.1 was contacted. Topic discovery did not check at all.
"""
import socket, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import ingest
from engine import topic_web


class _Quiet(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits.append(self.path)
        loc = getattr(self.server, "location", "")
        if loc and self.path.startswith("/go"):
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<html><title>Secret</title><p>internal secret words</p></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
    httpd.hits = []
    httpd.location = ""
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class FetchSSRFTest(unittest.TestCase):
    def setUp(self):
        self._gai = socket.getaddrinfo
        self._connect = socket.socket.connect
        self.tried = []

    def tearDown(self):
        socket.getaddrinfo = self._gai
        socket.socket.connect = self._connect

    def _refuse_connect(self):
        def connect(sock, addr):
            self.tried.append(addr[0])
            raise ConnectionRefusedError(addr)
        socket.socket.connect = connect

    def test_dns_rebinding_does_not_connect_to_loopback(self):
        calls = {"n": 0}

        def gai(host, port, *args, **kwargs):
            if host == "rebind.test":
                calls["n"] += 1
                ip = "8.8.8.8" if calls["n"] == 1 else "127.0.0.1"
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
            return self._gai(host, port, *args, **kwargs)

        socket.getaddrinfo = gai
        self._refuse_connect()
        with self.assertRaises(ingest.IngestError):
            ingest.fetch("http://rebind.test/secret")
        self.assertNotIn("127.0.0.1", self.tried)
        self.assertIn("8.8.8.8", self.tried)

    def test_cgnat_and_metadata_addresses_are_not_contacted(self):
        self._refuse_connect()
        for url in ("http://100.64.0.1/latest", "http://100.100.100.200/latest/meta-data"):
            with self.assertRaises(ingest.IngestError) as caught:
                ingest.fetch(url)
            self.assertIn("refusing", str(caught.exception))
        self.assertFalse(self.tried, self.tried)

    def test_link_local_metadata_and_loopback_and_bad_schemes_stay_refused(self):
        self._refuse_connect()
        for url in ("http://169.254.169.254/", "http://127.0.0.1/", "http://[::1]/", "file:///etc/passwd"):
            with self.assertRaises(ingest.IngestError):
                ingest.fetch(url)
        self.assertFalse(self.tried, self.tried)

    def test_redirect_to_cgnat_is_not_contacted(self):
        httpd = _server()
        httpd.location = "http://100.64.0.1/meta"
        port = httpd.server_address[1]

        def gai(host, port_, *args, **kwargs):
            if host == "public.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port_))]
            return self._gai(host, port_, *args, **kwargs)

        def connect(sock, addr):
            self.tried.append(addr[0])
            if addr[0] == "8.8.8.8":
                return self._connect(sock, ("127.0.0.1", port))
            raise ConnectionRefusedError(addr)

        socket.getaddrinfo = gai
        socket.socket.connect = connect
        try:
            with self.assertRaises(ingest.IngestError):
                ingest.fetch("http://public.test/go")
        finally:
            httpd.shutdown()
        self.assertNotIn("100.64.0.1", self.tried)
        self.assertNotIn("/meta", httpd.hits)

    def test_topic_discovery_does_not_read_loopback(self):
        httpd = _server()
        port = httpd.server_address[1]
        try:
            with self.assertRaises(ingest.IngestError):
                topic_web._open(f"http://127.0.0.1:{port}/secret", "text/html", timeout=2)
        finally:
            httpd.shutdown()
        self.assertEqual(httpd.hits, [])

    def test_documented_proxy_range_is_still_allowed(self):
        def gai(host, port, *args, **kwargs):
            if host == "example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", port))]
            return self._gai(host, port, *args, **kwargs)

        socket.getaddrinfo = gai
        self._refuse_connect()
        with self.assertRaises(ingest.IngestError):
            ingest.fetch("http://example.com/")
        self.assertIn("198.18.0.1", self.tried)
