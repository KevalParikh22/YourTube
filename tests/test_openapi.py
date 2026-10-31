"""
Tests for OpenAPI 3.1 specification and route parity with dashboard.py.
"""
import json
import os
import re
import unittest
from http.server import ThreadingHTTPServer
import threading
import urllib.request
import urllib.error
import subprocess
import sys

import dashboard

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(ROOT_DIR, "docs", "openapi.json")
DASHBOARD_PATH = os.path.join(ROOT_DIR, "yourtube", "dashboard.py")

ALLOWED_TAGS = {
    "Library", "Videos", "Categories", "AI", "Ask", "Search",
    "Knowledge", "Jobs", "Local AI", "Files", "Tools", "Export"
}


def load_spec():
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_routes_from_spec(spec):
    routes = set()
    for path, methods in spec.get("paths", {}).items():
        for method in ("get", "post", "put", "delete", "patch"):
            if method in methods:
                routes.add((method.upper(), path))
    return routes


def extract_routes_from_dashboard(source):
    routes = set()

    # 1. GET routes: url.path comparisons in _do_get
    get_match = re.search(r'def _do_get\(self\):(.*?)(?=def _do_post\(self\):)', source, re.DOTALL)
    if get_match:
        get_code = get_match.group(1)
        for match in re.finditer(r'url\.path\s*(?:==\s*["\'](/api/[^"\'\s)]+)["\']|in\s*\(([^)]+)\))', get_code):
            if match.group(1):
                routes.add(("GET", match.group(1)))
            elif match.group(2):
                for p in re.findall(r'["\'](/api/[^"\'\s)]+)["\']', match.group(2)):
                    routes.add(("GET", p))

    # 2. POST routes: path comparisons in _do_post
    post_match = re.search(r'def _do_post\(self\):(.*?)(?=def _ai\(self,)', source, re.DOTALL)
    if post_match:
        post_code = post_match.group(1)
        for match in re.finditer(r'path\s*(?:==\s*["\'](/api/[^"\'\s)]+)["\']|in\s*\(([^)]+)\))', post_code):
            if match.group(1):
                routes.add(("POST", match.group(1)))
            elif match.group(2):
                for p in re.findall(r'["\'](/api/[^"\'\s)]+)["\']', match.group(2)):
                    routes.add(("POST", p))

    # 3. Helper routes: path comparisons in _ai (prefix-dispatched group)
    ai_match = re.search(r'def _ai\(self,[^)]*\):(.*?)(?=\ndef [a-zA-Z0-9_]+|\Z)', source, re.DOTALL)
    if ai_match:
        ai_code = ai_match.group(1)
        for match in re.finditer(r'path\s*(?:==\s*["\'](/api/[^"\'\s)]+)["\']|in\s*\(([^)]+)\))', ai_code):
            if match.group(1):
                routes.add(("POST", match.group(1)))
            elif match.group(2):
                for p in re.findall(r'["\'](/api/[^"\'\s)]+)["\']', match.group(2)):
                    routes.add(("POST", p))

    return routes


class OpenApiTest(unittest.TestCase):
    def test_default_paths_resolve_from_repo_root(self):
        env = os.environ.copy()
        for key in ("YTKB_DB", "YTKB_DATA_DIR", "YTKB_SETTINGS", "YTKB_TAXONOMY", "YTKB_LOG_DIR"):
            env.pop(key, None)
        code = (
            f"import sys; sys.path.insert(0, {os.path.join(ROOT_DIR, 'yourtube')!r}); "
            "import os, storage, dashboard; "
            f"root = {ROOT_DIR!r}; "
            "assert storage.DB_PATH == os.path.join(root, 'yt_kb.db'), storage.DB_PATH; "
            "assert dashboard.WEB_DIR == os.path.join(root, 'web'), dashboard.WEB_DIR"
        )
        subprocess.run([sys.executable, "-c", code], cwd=ROOT_DIR, env=env, check=True)

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_spec_valid_json_and_openapi_shape(self):
        """The OpenAPI spec must be valid JSON and adhere to OpenAPI 3.1 structure."""
        self.assertTrue(os.path.isfile(SPEC_PATH), f"Missing {SPEC_PATH}")
        spec = load_spec()
        self.assertTrue(spec.get("openapi", "").startswith("3.1"), "Must be OpenAPI 3.1.x")

        # Info
        info = spec.get("info", {})
        self.assertIn("title", info)
        self.assertIn("version", info)
        self.assertIn("description", info)

        # Components & Security
        components = spec.get("components", {})
        schemes = components.get("securitySchemes", {})
        self.assertIn("cookieAuth", schemes)
        self.assertIn("headerAuth", schemes)
        self.assertEqual(schemes["headerAuth"]["name"], "X-YTKB")

        # Paths
        paths = spec.get("paths", {})
        self.assertGreater(len(paths), 0, "Spec must contain path items")

        for path_name, path_item in paths.items():
            self.assertTrue(path_name.startswith("/api/"), f"API spec should only describe /api/ paths: {path_name}")
            for method in ("get", "post"):
                if method in path_item:
                    op = path_item[method]
                    self.assertIn("summary", op, f"Missing summary on {method.upper()} {path_name}")
                    self.assertTrue(len(op["summary"].strip()) > 0)
                    self.assertIn("tags", op, f"Missing tags on {method.upper()} {path_name}")
                    for tag in op["tags"]:
                        self.assertIn(tag, ALLOWED_TAGS, f"Unknown tag '{tag}' on {method.upper()} {path_name}")
                    self.assertIn("responses", op, f"Missing responses on {method.upper()} {path_name}")
                    self.assertIn("200", op["responses"], f"Missing 200 response on {method.upper()} {path_name}")
                    if method == "post":
                        # Check headerAuth is in security for POST
                        sec = op.get("security", spec.get("security", []))
                        has_header_auth = any("headerAuth" in s for s in sec)
                        self.assertTrue(has_header_auth, f"POST {path_name} must require X-YTKB headerAuth")

    def test_parity_between_dashboard_and_openapi_spec(self):
        """Every route compared in dashboard.py must be documented in docs/openapi.json."""
        with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
            code = f.read()

        code_routes = extract_routes_from_dashboard(code)
        spec = load_spec()
        spec_routes = extract_routes_from_spec(spec)

        missing_in_spec = code_routes - spec_routes
        extra_in_spec = spec_routes - code_routes

        msg = []
        if missing_in_spec:
            msg.append(f"Routes implemented in dashboard.py but missing from docs/openapi.json: {sorted(missing_in_spec)}")
        if extra_in_spec:
            msg.append(f"Routes in docs/openapi.json but not implemented in dashboard.py: {sorted(extra_in_spec)}")

        self.assertEqual(code_routes, spec_routes, "\n".join(msg))

    def test_parity_fails_when_route_added_without_docs(self):
        """The parity checker must fail when an undocumented route is present in dashboard.py."""
        spec = load_spec()
        spec_routes = extract_routes_from_spec(spec)

        # 1. Undocumented GET route in code
        fake_get_code = """
def _do_get(self):
    if url.path == "/api/undocumented/get_route":
        return self._json({})
def _do_post(self):
    pass
def _ai(self, path, body, lib=1):
    pass
"""
        code_routes = extract_routes_from_dashboard(fake_get_code)
        self.assertIn(("GET", "/api/undocumented/get_route"), code_routes)
        with self.assertRaises(AssertionError):
            self.assertEqual(code_routes, spec_routes)

        # 2. Undocumented POST route in code
        fake_post_code = """
def _do_get(self):
    pass
def _do_post(self):
    if path == "/api/undocumented/post_route":
        return self._json({})
def _ai(self, path, body, lib=1):
    pass
"""
        code_routes = extract_routes_from_dashboard(fake_post_code)
        self.assertIn(("POST", "/api/undocumented/post_route"), code_routes)
        with self.assertRaises(AssertionError):
            self.assertEqual(code_routes, spec_routes)

        # 3. Undocumented helper POST route in _ai
        fake_ai_code = """
def _do_get(self):
    pass
def _do_post(self):
    pass
def _ai(self, path, body, lib=1):
    if path == "/api/ai/undocumented_ai":
        return self._json({})
"""
        code_routes = extract_routes_from_dashboard(fake_ai_code)
        self.assertIn(("POST", "/api/ai/undocumented_ai"), code_routes)
        with self.assertRaises(AssertionError):
            self.assertEqual(code_routes, spec_routes)

    def test_dashboard_serves_openapi_spec(self):
        """GET /api/openapi.json returns 200 and matches the spec file."""
        req = urllib.request.Request(self.base + "/api/openapi.json")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("application/json", resp.headers.get("Content-Type", ""))
            data = json.loads(resp.read().decode("utf-8"))

        spec = load_spec()
        self.assertEqual(data["openapi"], spec["openapi"])
        self.assertEqual(data["info"]["title"], spec["info"]["title"])
        self.assertEqual(set(data["paths"].keys()), set(spec["paths"].keys()))

    def test_dashboard_serves_openapi_requires_token(self):
        """GET /api/openapi.json requires token when dashboard.TOKEN is set."""
        old_token = dashboard.TOKEN
        try:
            dashboard.TOKEN = "secret-token-12345"
            # Without token -> 401
            req = urllib.request.Request(self.base + "/api/openapi.json")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 401)

            # With token cookie -> 200
            req_auth = urllib.request.Request(self.base + "/api/openapi.json",
                                              headers={"Cookie": f"{dashboard.TOKEN_COOKIE}=secret-token-12345"})
            with urllib.request.urlopen(req_auth) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            dashboard.TOKEN = old_token


if __name__ == "__main__":
    unittest.main()
