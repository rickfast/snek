"""Check tutorial programs, source inclusion, and the built site's local links."""
from html.parser import HTMLParser
from pathlib import Path
import os
import subprocess
import sys
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from snekc import driver
from snekc.wasm import encode
from docs.tools.hooks import rebase_links, sections

BASE = "https://rickfast.github.io/snek/"
EXAMPLES = {
    "hello.snek": (b"", b"Hello, WebAssembly!\n55\n"),
    "word-count.snek": (b"", b"hello: 2\nsnek: 1\nstrasse: 2\n"),
    "streams.snek": (b"hello\n\x00\xff" * 1000, b"hello\n\x00\xff" * 1000),
}


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])


def check_links(directory):
    pages = {path: Page(path.read_text(encoding="utf-8"))
             for path in directory.rglob("*.html")}
    if len(pages) < 25:
        raise AssertionError("Expected the complete documentation site; run mkdocs build --strict")
    checked = 0
    failures = []
    for path, page in pages.items():
        relative = path.relative_to(directory).as_posix()
        origin = BASE + relative
        for href in page.links:
            target = urljoin(origin, href)
            if not target.startswith(BASE):
                continue
            parsed = urlsplit(target)
            local = unquote(parsed.path[len(urlsplit(BASE).path):])
            destination = (directory / local).resolve()
            if not destination.is_relative_to(directory):
                failures.append(f"{relative}: link escapes site: {href}")
                continue
            if destination.is_dir():
                destination /= "index.html"
            if not destination.is_file():
                failures.append(f"{relative}: missing target: {href}")
            elif parsed.fragment and destination in pages:
                anchor = unquote(parsed.fragment)
                if anchor not in pages[destination].ids:
                    failures.append(f"{relative}: missing anchor: {href}")
            checked += 1
    if failures:
        raise AssertionError("\n".join(failures))
    index = directory / "search/search_index.json"
    if not index.is_file():
        raise AssertionError("Missing client-side search index")
    if any((directory / name).exists() for name in ("tools", "overrides", "requirements.txt", "requirements.in")):
        raise AssertionError("Documentation build tooling leaked into the published site")
    compiler = directory / "assets/playground/generated/compiler.wasm"
    if not compiler.is_file() or compiler.read_bytes() != (ROOT / "bootstrap/build/compiler.wasm").read_bytes():
        raise AssertionError("Missing or stale playground compiler; run docs/tools/prepare_playground.py before building")
    print(f"{len(pages)} HTML pages; {checked} local links/assets/anchors verified")


def check_examples():
    found = {path.name for path in (ROOT / "docs/examples").glob("*.snek")}
    if found != set(EXAMPLES):
        raise AssertionError("Register expected I/O for every documentation example")
    for name, (stdin, stdout) in EXAMPLES.items():
        path = ROOT / "docs/examples" / name
        source = path.read_text(encoding="utf-8")
        for frontend in ("cpython", "snek"):
            wasm = encode(driver.compile_to_wat(source, str(path), frontend=frontend, compiler="python"))
            if not wasm.startswith(b"\0asm"):
                raise AssertionError("Not a Wasm binary: " + name)
        result = subprocess.run([sys.executable, str(path)], input=stdin, capture_output=True,
                                env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=30)
        if (result.returncode, result.stdout, result.stderr) != (0, stdout, b""):
            raise AssertionError(f"{name}: unexpected CPython behavior: {result}")
        print(f"{name}: both reference frontends compile; CPython output verified")


def check_inclusion():
    sample = "# Title\n## First\n```sh\n## Not a heading\n```\nbody\n## Second\nend\n"
    assert set(sections(sample)) == {"First", "Second"}
    assert "Not a heading" in sections(sample)["First"]
    assert rebase_links("[source](../DESIGN.md#numbers)", "bench/README.md") == (
        "[source](https://github.com/rickfast/snek/blob/main/DESIGN.md#numbers)")
    assert rebase_links("[local](#here) [web](https://example.com)", "README.md") == (
        "[local](#here) [web](https://example.com)")
    try:
        rebase_links("[missing](does-not-exist.md)", "README.md")
    except ValueError:
        pass
    else:
        raise AssertionError("Missing source links must fail")
    print("Source inclusion and link rebasing verified")


if __name__ == "__main__":
    for name in ("SNEK_MACHINE_LOWERER", "SNEK_MACHINE_EMITTER"):
        os.environ.pop(name, None)
    check_inclusion()
    check_examples()
    check_links((ROOT / "site").resolve())
