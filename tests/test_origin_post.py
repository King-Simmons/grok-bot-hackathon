"""State-changing requests reject a null or malformed Origin.

A missing Origin stays allowed so curl and the smoke scripts keep working.
GET keeps the PR #30 behavior, including an allow-and-log for Origin null.
"""
from tests.server_case import ServerCase


class OriginPostTest(ServerCase):
    def post(self, origin):
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        return self.request("POST", "/api/synthesize", body=b"{}", headers=headers)

    def test_null_and_malformed_origin_are_forbidden_on_post(self):
        for origin in ("null", "NULL", "not a url", "http://", "", "file://x/y"):
            status, _loc, raw, body = self.post(origin)
            self.assertEqual(status, 403, (origin, raw))
            self.assertEqual(body["error"], "forbidden", origin)

    def test_missing_origin_still_reaches_the_route(self):
        status, _loc, _raw, body = self.post(None)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "missing url")
        status, _loc, _raw, body = self.post_json("/api/agents", {"id": "origin-check"})
        self.assertNotEqual(status, 403)
        self.assertNotEqual(body.get("error"), "forbidden")

    def test_get_null_origin_stays_allowed(self):
        status, _loc, _raw, body = self.request("GET", "/api/health", headers={"Origin": "null"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_foreign_origin_stays_forbidden(self):
        status, _loc, _raw, body = self.post("https://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "forbidden")
