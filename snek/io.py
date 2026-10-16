"""Stream types for annotations; CLI instances are supplied by snek.sys.

Construction of arbitrary streams is not yet supported by the compiler.
"""
from .sys import BinaryInput, BinaryOutput, TextInput, TextOutput

__all__ = ["BinaryInput", "BinaryOutput", "TextInput", "TextOutput"]
