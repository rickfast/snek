# Contributing and maintaining the docs

Start with a small reproducer or fixture. Compiler changes should preserve both
accepted behavior and specific rejection diagnostics, not just whether a program
builds. Keep the Python reference path available for differential tests.

## Choose the right validation

```sh
python tests/run_suites.py fast
python tests/run_suites.py all --list
```

Use the [test suite guide](testing.md) to select the affected language, bootstrap,
component, native, or packaging checks. Rebuild the full compiler after changing
its sources; use `python packaging/build_native.py` before testing a fresh native
executable. Do not treat a previously built compiler as proof of new source.

## Preview this documentation

From the repository root, using Python 3.13:

```sh
python -m venv .venv-docs
# macOS / Linux:
source .venv-docs/bin/activate
# Windows PowerShell instead: .venv-docs\Scripts\Activate.ps1
python -m pip install --require-hashes -r docs/requirements.txt
python -m mkdocs serve
```

Use the local URL printed by MkDocs. For a production build and verification:

```sh
python -m mkdocs build --strict
python docs/tools/check.py
```

The docs dependencies are separate from Snek's development/runtime dependencies.
The static output is in ignored `site/`; no compiler bootstrap or Rust build is
needed to build documentation. Fonts, styling, and search run locally in the
published site, without analytics or a search service.

## Where to edit

Tutorials and navigation live under `docs/` and in `mkdocs.yml`. Detailed reference
pages include selected sections of `DESIGN.md`, `README.md`, and the existing
bootstrap, benchmark, packaging, and test READMEs using `source_doc` /
`source_sections` front matter. Edit those source documents to update the included
text. Renaming an included heading without updating its selector fails the build.

Tutorial code lives in `docs/examples/*.snek`. The `<!-- example: hello.snek -->`
directive includes that exact file in a page. The checker compiles each tutorial
through both reference-parser choices, executes it under CPython, and validates
the generated site's internal links, anchors, and local assets. It does not claim
to run the full compiler regression suite.

## Deployment

`.github/workflows/docs.yml` builds and checks documentation on relevant pushes
and pull requests. Only a successful build on `main` can deploy, using GitHub's
Pages artifact and deployment actions. Pull requests never receive Pages write
permissions. The workflow also supports a manual run.

In repository **Settings → Pages**, the publishing source must be **GitHub
Actions**. The public project URL is <https://rickfast.github.io/snek/>. No custom
domain, generated `gh-pages` branch, or registry publication is required.

Dependencies are fully pinned with hashes. To intentionally refresh them, update
`docs/requirements.in` and regenerate the lock with:

```sh
uv pip compile docs/requirements.in --generate-hashes --output-file docs/requirements.txt
```

Then rebuild, run the checker, and preview desktop/mobile navigation and search
before publishing.
