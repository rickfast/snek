"""Load authored Snek resources and resolve the private Unicode constant ABI.

Only NAME tokens are substituted: comments and string literals are untouched.
The prepared source has the same line positions as the authored resource.
Bootstrap embeds this source once; self-hosted stages do not read resources.
"""
from importlib.resources import files
import io
import tokenize

from .unicode import FLAG_NAMES


def resolve_unicode_constants(source: str) -> str:
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string.startswith("__unicode_"):
            name = token.string[len("__unicode_"):]
            if name not in FLAG_NAMES:
                raise ValueError("unknown runtime Unicode constant: " + name)
            token = token._replace(type=tokenize.NUMBER, string=str(FLAG_NAMES[name]))
        tokens.append(token)
    return tokenize.untokenize(tokens)


def load_runtime_source(name: str, *, unicode_constants: bool = False) -> str:
    if name not in {"exceptions.snek", "strings.snek", "formatting.snek"}:
        raise ValueError("unknown runtime source: " + name)
    source = files("snekc").joinpath("runtime", name).read_text(encoding="utf-8")
    return resolve_unicode_constants(source) if unicode_constants else source
