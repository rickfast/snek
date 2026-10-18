"""Run the real documentation playground and its self-hosted compiler in browsers."""
from functools import partial
import hashlib
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docs.tools.prepare_playground import prepare
from docs.tools import prepare_playground


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class PlaygroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        prepare()
        directory = tempfile.TemporaryDirectory(prefix="snek-playground-")
        cls.addClassCleanup(directory.cleanup)
        site = Path(directory.name) / "snek"
        subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
                       cwd=ROOT, check=True, capture_output=True, timeout=60)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=directory.name))
        cls.addClassCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(lambda: (server.shutdown(), thread.join()))
        cls.url = f"http://127.0.0.1:{server.server_port}/snek/learn/playground/"
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        engine = os.environ.get("SNEK_BROWSER", "chromium")
        if engine not in ("chromium", "firefox", "webkit"):
            raise ValueError("SNEK_BROWSER must be chromium, firefox, or webkit")
        cls.browser = getattr(playwright, engine).launch()
        cls.addClassCleanup(cls.browser.close)
        print(f"Playground browser: {engine} {cls.browser.version}", flush=True)

    def setUp(self):
        context = self.browser.new_context(accept_downloads=True)
        self.addCleanup(context.close)
        self.context = context
        self.page = context.new_page()
        self.page.set_default_timeout(30000)
        self.page.goto(self.url)
        self.page.wait_for_function('!document.querySelector("#snek-run").disabled')

    def run_source(self, source=None):
        if source is not None:
            self.page.locator("#snek-source").fill(source)
        self.page.locator("#snek-run").click()
        self.page.wait_for_function('document.querySelector("#snek-playground").dataset.state === "idle"')
        return self.page.locator("#snek-output").text_content()

    def test_examples_download_and_no_source_upload(self):
        requests = []
        self.context.on("request", lambda request: requests.append((request.method, request.url)))
        self.assertEqual(self.run_source(), "Hello, WebAssembly!\n55\n")
        with self.page.expect_download() as event:
            self.page.locator("#snek-download").click()
        self.assertTrue(Path(event.value.path()).read_bytes().startswith(b"\0asm\1\0\0\0"))
        for name, expected in (("word-count", "hello: 2\nsnek: 1\nstrasse: 2\n"),
                               ("streams", "Hello from stdin, 🐍!\n")):
            self.page.locator("#snek-example").select_option(name)
            self.page.wait_for_function('!document.querySelector("#snek-run").disabled')
            self.assertEqual(self.run_source(), expected)
        self.assertTrue(all(method == "GET" and url.startswith(self.url.split("/snek/")[0]) for method, url in requests), requests)

    def test_diagnostics_and_recovery(self):
        self.assertIn("missing", self.run_source("print(missing)"))
        self.assertTrue(self.page.locator("#snek-download").is_disabled())
        self.assertIn("ValueError: broken", self.run_source('raise ValueError("broken")'))
        self.assertEqual(self.run_source('print("recovered 🐍")'), "recovered 🐍\n")

    def test_stop_timeout_and_recovery(self):
        self.page.locator("#snek-source").fill("while True:\n    pass\n")
        self.page.locator("#snek-run").click()
        self.page.wait_for_function('document.querySelector("#snek-status").textContent.includes("Running")')
        self.page.locator("#snek-stop").click()
        self.assertIn("Stopped", self.page.locator("#snek-status").text_content())
        self.run_source()
        self.assertIn("timed out", self.page.locator("#snek-status").text_content())
        self.assertEqual(self.run_source("print(42)"), "42\n")

    def test_output_limit_unsupported_host_and_input_limit(self):
        self.assertIn("Output limit exceeded", self.run_source('while True:\n    print("x" * 10000)\n'))
        self.assertIn("does not provide", self.run_source("from snek import time\nprint(time.time())"))
        self.page.locator("#snek-source").fill("#" + "x" * 65536)
        self.assertIn("64 KiB", self.run_source())
        self.assertEqual(self.run_source("print(42)"), "42\n")

    def test_compiler_fetch_failure_and_recovery(self):
        self.context.route("**/generated/compiler.wasm", lambda route: route.fulfill(status=503, body="Unavailable"))
        self.assertIn("HTTP 503", self.run_source())
        self.context.unroute("**/generated/compiler.wasm")
        self.assertEqual(self.run_source("print(42)"), "42\n")

    def test_mobile_layout_and_keyboard(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.page.locator("#snek-source").fill('print("<script>not HTML</script>")')
        self.page.locator("#snek-source").press("Control+Enter")
        self.page.wait_for_function('document.querySelector("#snek-status").textContent === "Finished."')
        self.assertEqual(self.page.locator("#snek-output").text_content(), "<script>not HTML</script>\n")
        self.assertEqual(self.page.locator("#snek-output script").count(), 0)
        screenshot = os.environ.get("SNEK_PLAYGROUND_SCREENSHOT")
        if screenshot:
            self.page.screenshot(path=screenshot, full_page=True)

    def test_asset_preparation_rejects_stale_and_corrupt_compilers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "bootstrap/build"
            build.mkdir(parents=True)
            manifest = {"source_sha256": "stale", "stage2_equals_stage3": True,
                        "host_fallback_guards": True, "host_passes_after_seed": False,
                        "stages": [{"file": "compiler.stage2.wasm", "sha256": "corrupt"}]}
            path = build / "compiler-manifest.json"
            path.write_text(json.dumps(manifest))
            (build / "compiler.wasm").write_bytes(b"wasm")
            (build / "compiler.stage2.wasm").write_bytes(b"wasm")
            with patch.object(prepare_playground, "ROOT", root), patch.object(prepare_playground, "compiler_source", return_value="source"):
                with self.assertRaisesRegex(RuntimeError, "Stale compiler"):
                    prepare()
                manifest["source_sha256"] = hashlib.sha256(b"source").hexdigest()
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    prepare()


if __name__ == "__main__":
    unittest.main()
