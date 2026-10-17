"""Include authoritative repository docs and executable examples in the site.

Front matter selects a source and optional level-two sections. Missing sources
or renamed headings fail the build instead of silently publishing empty pages.
Only source-document links are rebased; hand-authored site links stay relative.
"""
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/rickfast/snek/blob/main/"
SOURCES = {"README.md", "DESIGN.md", "bootstrap/README.md", "bench/README.md",
           "packaging/README.md", "tests/README.md"}
EXAMPLE = re.compile(r"^<!-- example: ([a-z0-9-]+\.snek) -->$", re.MULTILINE)
LINK = re.compile(r"(?<!!)(\[[^\]\n]+\]\()([^\s)]+)(\))")


def sections(text):
    """Index actual H2 headings, ignoring heading-like lines in fenced code."""
    found = {}
    current = None
    fence = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        if fence is None and line.startswith("## "):
            current = line[3:].strip()
            if current in found:
                raise ValueError("Duplicate documentation heading: " + current)
            found[current] = []
        if current is not None:
            found[current].append(line)
    return {key: "".join(lines).rstrip() for key, lines in found.items()}


def rebase_links(text, source):
    def replace(match):
        target = match[2]
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or target.startswith("#"):
            return match[0]
        path = (ROOT / source).parent / parsed.path
        relative = path.resolve().relative_to(ROOT)
        if not path.exists():
            raise ValueError(f"Broken source link in {source}: {target}")
        url = REPO + quote(relative.as_posix(), safe="/")
        if parsed.fragment:
            url += "#" + parsed.fragment
        return match[1] + url + match[3]
    return LINK.sub(replace, text)


def on_page_markdown(markdown, page, **kwargs):
    source = page.meta.get("source_doc")
    if source:
        if source not in SOURCES:
            raise ValueError("Unapproved documentation source: " + source)
        text = (ROOT / source).read_text(encoding="utf-8")
        selected = page.meta.get("source_sections")
        if selected:
            indexed = sections(text)
            text = "\n\n".join(indexed[heading] for heading in selected)
        else:
            # The site wrapper supplies its own H1.
            text = text.split("\n", 1)[1]
        markdown += (f"\n\n<div class=\"source-note\">Reference maintained in "
                     f"<a href=\"{REPO}{source}\">{source}</a>.</div>\n\n"
                     + rebase_links(text, source))

    def example(match):
        path = ROOT / "docs/examples" / match[1]
        source = path.read_text(encoding="utf-8").rstrip()
        return f'```python title="{match[1]}"\n{source}\n```'

    return EXAMPLE.sub(example, markdown)
