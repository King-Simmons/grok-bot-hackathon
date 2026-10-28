"""Non-ASCII URLs must be ASCII before DNS and before the request."""
import socket, unittest, urllib.error, urllib.request

from engine import ingest


class FetchAsciiTest(unittest.TestCase):
    def setUp(self):
        self.hosts = []
        self.seen = []
        self._gai = socket.getaddrinfo
        self._open = urllib.request.OpenerDirector.open

        def gai(host, port, *args, **kwargs):
            self.hosts.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port or 0))]

        def open_req(opener, req, *args, **kwargs):
            self.seen.append(req.full_url)
            req.full_url.encode("ascii")
            raise urllib.error.URLError("stopped")

        socket.getaddrinfo = gai
        urllib.request.OpenerDirector.open = open_req

    def tearDown(self):
        socket.getaddrinfo = self._gai
        urllib.request.OpenerDirector.open = self._open

    def _fetch(self, url):
        with self.assertRaises(ingest.IngestError) as caught:
            ingest.fetch(url)
        self.assertNotIn("ascii", str(caught.exception))
        self.assertEqual(len(self.seen), 1, self.seen)
        return self.seen[0]

    def test_non_ascii_path_and_query_are_percent_encoded(self):
        url = self._fetch("https://ja.wikipedia.org/wiki/東京都?q=大阪")
        self.assertIn("/wiki/%E6%9D%B1%E4%BA%AC%E9%83%BD", url)
        self.assertIn("q=%E5%A4%A7%E9%98%AA", url)
        self.assertNotIn("東京都", url)
        self.assertNotIn("大阪", url)
        self.assertIn("ja.wikipedia.org", self.hosts)

    def test_existing_percent_escapes_stay(self):
        url = self._fetch("https://example.com/wiki/%E6%9D%B1%E4%BA%AC%E9%83%BD?q=%E5%A4%A7%E9%98%AA")
        self.assertIn("/wiki/%E6%9D%B1%E4%BA%AC%E9%83%BD", url)
        self.assertIn("q=%E5%A4%A7%E9%98%AA", url)
        self.assertNotIn("%25E6", url)
        self.assertNotIn("%25", url)

    def test_unicode_host_is_idna_before_dns_and_the_request(self):
        url = self._fetch("https://münchen.de/stadt")
        host = "münchen".encode("idna").decode("ascii")
        self.assertIn(host, self.hosts)
        self.assertIn(host, url)
        self.assertNotIn("ü", url)
        self.assertNotIn("ü", "".join(self.hosts))
