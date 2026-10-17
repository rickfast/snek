"""Snek source frontend. Reads UTF-8 Python-subset source and prints an owned AST.

This module is itself Snek: no ast, tokenize, regex, or host parser dependencies.
Numbers retain their spelling for the future typed checker. Locations are
one-based lines and zero-based UTF-8 byte columns, like CPython's AST.
"""
from __future__ import annotations
from snek import i32, read_bytes
from bootstrap.unicode_names import unicode_ideograph, unicode_name_table


def fail(message: str, line: i32, column: i32) -> None:
    raise ValueError("line " + str(line) + ", column " + str(column) + ": " + message)


def utf8(data: list[i32]) -> str:
    out: list[str] = []
    pos = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value = byte
        count = 0
        minimum = 0
        if byte >= 194 and byte <= 223:
            value = byte & 31
            count = 1
            minimum = 128
        elif byte >= 224 and byte <= 239:
            value = byte & 15
            count = 2
            minimum = 2048
        elif byte >= 240 and byte <= 244:
            value = byte & 7
            count = 3
            minimum = 65536
        elif byte >= 128:
            fail("invalid UTF-8 source", 1, pos - 1)
        for unused in range(count):
            if pos >= len(data) or data[pos] < 128 or data[pos] > 191:
                fail("invalid UTF-8 source", 1, pos)
            value = (value << 6) | (data[pos] & 63)
            pos += 1
        if value < minimum or value > 1114111 or (value >= 55296 and value <= 57343):
            fail("invalid UTF-8 source", 1, pos)
        out.append(chr(value))
    return "".join(out)


def json_string(value: str) -> str:
    out: list[str] = ['"']
    digits = "0123456789abcdef"
    for char in value:
        code = ord(char)
        if char == '"' or char == "\\":
            out.append("\\" + char)
        elif code < 32:
            out.append("\\u00" + digits[code >> 4] + digits[code & 15])
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


class Node:
    kind: str
    text: str
    keys: list[str]
    items: list[Node]
    line: i32
    column: i32

    def __init__(self, kind: str, text: str = "", line: i32 = 0, column: i32 = 0) -> None:
        self.kind = kind
        self.text = text
        self.keys = []
        self.items = []
        self.line = line
        self.column = column

    def put(self, key: str, value: Node) -> Node:
        self.keys.append(key)
        self.items.append(value)
        return self

    def get(self, key: str) -> Node:
        for index in range(len(self.keys)):
            if self.keys[index] == key:
                return self.items[index]
        raise ValueError("missing AST field: " + key)

    def render(self, out: list[str]) -> None:
        if self.kind == "str":
            out.append(json_string(self.text))
        elif self.kind == "none":
            out.append("null")
        elif self.kind == "scalar":
            out.append(self.text)
        elif self.kind == "list":
            out.append("[")
            for index in range(len(self.items)):
                if index > 0:
                    out.append(",")
                self.items[index].render(out)
            out.append("]")
        else:
            out.append('{"_type":' + json_string(self.kind))
            if self.line > 0:
                out.append(',"_line":' + str(self.line) + ',"_column":' + str(self.column))
            for field in range(len(self.keys)):
                out.append("," + json_string(self.keys[field]) + ":")
                self.items[field].render(out)
            out.append("}")

    def json(self) -> str:
        out: list[str] = []
        self.render(out)
        return "".join(out)


def string(value: str) -> Node:
    return Node("str", value)


def scalar(value: i32) -> Node:
    return Node("scalar", str(value))


def missing() -> Node:
    return Node("none")


def sequence(items: list[Node]) -> Node:
    result = Node("list")
    result.items = items
    return result


class Token:
    kind: str
    text: str
    line: i32
    column: i32

    def __init__(self, kind: str, text: str, line: i32, column: i32) -> None:
        self.kind = kind
        self.text = text
        self.line = line
        self.column = column

    def node(self, kind: str) -> Node:
        return Node(kind, "", self.line, self.column)

    def error(self, message: str) -> None:
        fail(message, self.line, self.column)


def offset_token(token: Token, text: str, offset: i32) -> Token:
    line = token.line
    column = token.column
    for char in text[:offset]:
        if char == "\n":
            line += 1
            column = 0
        else:
            value = ord(char)
            column += 1
            if value >= 128:
                column += 1
            if value >= 2048:
                column += 1
            if value >= 65536:
                column += 1
    return Token("position", "", line, column)


def letter(char: str) -> bool:
    return char != "" and ((char >= "a" and char <= "z") or (char >= "A" and char <= "Z") or char == "_")


def digit(char: str) -> bool:
    return char != "" and char >= "0" and char <= "9"


def hex_digit(char: str) -> i32:
    if digit(char):
        return ord(char) - 48
    if char >= "a" and char <= "f":
        return ord(char) - 87
    if char >= "A" and char <= "F":
        return ord(char) - 55
    return -1


def keyword(word: str) -> bool:
    return word in ["False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"]


def valid_digits(text: str, base: i32, prefix: bool) -> bool:
    start = 0
    if prefix and text.startswith("_"):
        start = 1
    previous = False
    for index in range(start, len(text)):
        char = text[index]
        if char == "_":
            if not previous:
                return False
            previous = False
        else:
            value = hex_digit(char)
            if value < 0 or value >= base:
                return False
            previous = True
    return previous


def valid_number(text: str) -> bool:
    lower = text.lower()
    if lower.startswith("0x"):
        return valid_digits(lower[2:], 16, True)
    if lower.startswith("0o"):
        return valid_digits(lower[2:], 8, True)
    if lower.startswith("0b"):
        return valid_digits(lower[2:], 2, True)
    exponent = lower.find("e")
    mantissa = lower
    if exponent >= 0:
        mantissa = lower[:exponent]
        power = lower[exponent + 1:]
        if power.startswith("+") or power.startswith("-"):
            power = power[1:]
        if not valid_digits(power, 10, False):
            return False
    point = mantissa.find(".")
    if point >= 0:
        left = mantissa[:point]
        right = mantissa[point + 1:]
        return (left != "" or right != "") and (left == "" or valid_digits(left, 10, False)) and (right == "" or valid_digits(right, 10, False))
    if not valid_digits(mantissa, 10, False):
        return False
    if exponent < 0 and mantissa.startswith("0"):
        for char in mantissa:
            if char != "0" and char != "_":
                return False
    return True


def named_unicode(name: str, token: Token) -> str:
    # Unicode lookup is ASCII case-insensitive, not Unicode case folding.
    for char in name:
        if ord(char) >= 128:
            token.error("unknown Unicode character name: " + name)
    upper = name.upper()
    if upper.startswith("CJK UNIFIED IDEOGRAPH-"):
        suffix = upper[22:]
        code = 0
        valid = len(suffix) >= 4 and len(suffix) <= 5 and not suffix.startswith("0")
        for char_digit in suffix:
            value = hex_digit(char_digit)
            if value < 0:
                valid = False
            code = code * 16 + value
        if valid and unicode_ideograph(code):
            return chr(code)
        token.error("unknown Unicode character name: " + name)
    if upper.startswith("HANGUL SYLLABLE "):
        rest = upper[16:]
        leading = ["G", "GG", "N", "D", "DD", "R", "M", "B", "BB", "S", "SS", "", "J", "JJ", "C", "K", "T", "P", "H"]
        vowels = ["A", "AE", "YA", "YAE", "EO", "E", "YEO", "YE", "O", "WA", "WAE", "OE", "YO", "U", "WEO", "WE", "WI", "YU", "EU", "YI", "I"]
        trailing = ["", "G", "GG", "GS", "N", "NJ", "NH", "D", "L", "LG", "LM", "LB", "LS", "LT", "LP", "LH", "M", "B", "BS", "S", "SS", "NG", "J", "C", "K", "T", "P", "H"]
        lead = 11
        for index in range(len(leading)):
            if rest.startswith(leading[index]) and len(leading[index]) > len(leading[lead]):
                lead = index
        rest = rest[len(leading[lead]):]
        vowel = -1
        for vowel_index in range(len(vowels)):
            if rest.startswith(vowels[vowel_index]):
                if vowel < 0 or len(vowels[vowel_index]) > len(vowels[vowel]):
                    vowel = vowel_index
        if vowel >= 0:
            rest = rest[len(vowels[vowel]):]
            for tail in range(len(trailing)):
                if rest == trailing[tail]:
                    return chr(44032 + (lead * 21 + vowel) * 28 + tail)
        token.error("unknown Unicode character name: " + name)
    data = unicode_name_table()
    low = 0
    high = len(data)
    while low < high:
        middle = (low + high) // 2
        start = data.rfind("\n", low, middle) + 1
        if start < low:
            start = low
        end = data.find("\n", start)
        equals = data.find("=", start)
        candidate = data[start:equals]
        if candidate == upper:
            result = 0
            for digit_index in range(equals + 1, end):
                result = result * 16 + hex_digit(data[digit_index])
            return chr(result)
        if candidate < upper:
            low = end + 1
        else:
            high = start
    token.error("unknown Unicode character name: " + name)
    return ""


class Lexer:
    source: str
    pos: i32
    line: i32
    column: i32
    tokens: list[Token]
    groups: list[str]
    indents: list[i32]
    alternate: list[i32]
    beginning: bool
    logical: bool

    def __init__(self, source: str) -> None:
        self.source = source.replace("\r\n", "\n").replace("\r", "\n")
        self.pos = 0
        self.line = 1
        self.column = 0
        self.tokens = []
        self.groups = []
        self.indents = [0]
        self.alternate = [0]
        self.beginning = True
        self.logical = False
        if "\x00" in self.source:
            self.error("source contains a null byte")
        if self.source.startswith("\ufeff"):
            self.source = self.source[1:]

    def peek(self, ahead: i32 = 0) -> str:
        index = self.pos + ahead
        if index < len(self.source):
            return self.source[index]
        return ""

    def take(self) -> str:
        char = self.peek()
        if char != "":
            self.pos += 1
            if char == "\n":
                self.line += 1
                self.column = 0
            else:
                code = ord(char)
                self.column += 1
                if code >= 128:
                    self.column += 1
                if code >= 2048:
                    self.column += 1
                if code >= 65536:
                    self.column += 1
        return char

    def error(self, message: str) -> None:
        fail(message, self.line, self.column)

    def add(self, kind: str, text: str, line: i32, column: i32) -> None:
        self.tokens.append(Token(kind, text, line, column))

    def indentation(self) -> None:
        width = 0
        alternate = 0
        while self.peek() in [" ", "\t", "\f"]:
            char = self.take()
            if char == " ":
                width += 1
                alternate += 1
            elif char == "\t":
                width = (width // 8 + 1) * 8
                alternate += 1
            else:
                width = 0
                alternate = 0
        self.beginning = False
        if self.peek() in ["", "\n", "#"] or len(self.groups) > 0:
            return
        current = self.indents[-1]
        if width > current:
            if alternate <= self.alternate[-1]:
                self.error("inconsistent tabs and spaces in indentation")
            self.indents.append(width)
            self.alternate.append(alternate)
            self.add("indent", "<indent>", self.line, self.column)
        else:
            while width < self.indents[-1]:
                self.indents.pop()
                self.alternate.pop()
                self.add("dedent", "<dedent>", self.line, self.column)
            if width != self.indents[-1]:
                self.error("unindent does not match an outer indentation level")
            if alternate != self.alternate[-1]:
                self.error("inconsistent tabs and spaces in indentation")

    def quoted(self, prefix: str) -> None:
        quote = self.take()
        triple = self.peek() == quote and self.peek(1) == quote
        if triple:
            self.take()
            self.take()
        formatted = "f" in prefix.lower()
        while self.peek() != "":
            char = self.take()
            if char == quote:
                if not triple:
                    return
                if self.peek() == quote and self.peek(1) == quote:
                    self.take()
                    self.take()
                    return
            elif char == "\n" and not triple:
                self.error("unterminated string literal")
            elif char == "\\":
                # Braces retain their f-string meaning even after a backslash.
                if not formatted or self.peek() not in ["{", "}"]:
                    self.take()
            elif formatted and char == "{":
                if self.peek() == "{":
                    self.take()
                else:
                    self.field_end(quote, triple)
            elif formatted and char == "}" and self.peek() == "}":
                self.take()
        self.error("unterminated string literal")

    def field_end(self, quote: str, triple: bool) -> None:
        stack: list[str] = []
        while self.peek() != "":
            char = self.peek()
            if char in ["'", '"']:
                self.quoted("")
            elif char == "#":
                while self.peek() not in ["", "\n"]:
                    self.take()
            elif char in ["(", "[", "{"]:
                stack.append(char)
                self.take()
            elif char in [")", "]", "}"]:
                self.take()
                if len(stack) == 0:
                    if char != "}":
                        self.error("unmatched delimiter in f-string")
                    return
                opener = stack.pop()
                if (opener == "(" and char != ")") or (opener == "[" and char != "]") or (opener == "{" and char != "}"):
                    self.error("mismatched delimiter in f-string")
            elif char == ":" and len(stack) == 0:
                self.take()
                self.format_end(quote, triple)
                return
            else:
                self.take()
        self.error("unterminated f-string field")

    def format_end(self, quote: str, triple: bool) -> None:
        while self.peek() != "":
            char = self.take()
            if char == "\\" and self.peek() not in ["{", "}"]:
                self.take()
                continue
            if char == "\n" and not triple:
                self.error("newline in a single-quoted f-string format specifier")
            if char == quote and (not triple or (self.peek() == quote and self.peek(1) == quote)):
                self.error("unterminated f-string format specifier")
            if char == "}":
                return
            if char == "{":
                self.field_end(quote, triple)
        self.error("unterminated f-string format specifier")

    def scan(self) -> list[Token]:
        while self.pos < len(self.source):
            if self.beginning:
                self.indentation()
            char = self.peek()
            line = self.line
            column = self.column
            start = self.pos
            if char == "":
                break
            if char in [" ", "\t", "\f"]:
                self.take()
                continue
            if char == "#":
                while self.peek() not in ["", "\n"]:
                    self.take()
                continue
            if char == "\n":
                self.take()
                if len(self.groups) == 0 and self.logical:
                    self.add("newline", "<newline>", line, column)
                    self.logical = False
                self.beginning = True
                continue
            if char == "\\":
                self.take()
                if self.take() != "\n":
                    self.error("expected newline after line continuation")
                if self.peek() == "":
                    self.error("unexpected EOF after line continuation")
                continue
            self.logical = True
            if letter(char):
                while letter(self.peek()) or digit(self.peek()):
                    self.take()
                word = self.source[start:self.pos]
                if self.peek() in ["'", '"'] and word.lower() in ["r", "u", "f", "fr", "rf", "b", "br", "rb"]:
                    if "b" in word.lower():
                        self.error("bytes literals are outside the Snek subset")
                    self.quoted(word)
                    self.add("string", self.source[start:self.pos], line, column)
                else:
                    self.add("name", word, line, column)
            elif char in ["'", '"']:
                self.quoted("")
                self.add("string", self.source[start:self.pos], line, column)
            elif digit(char) or (char == "." and digit(self.peek(1))):
                based = char == "0" and self.peek(1).lower() in ["x", "o", "b"]
                if based:
                    self.take()
                    self.take()
                    while letter(self.peek()) or digit(self.peek()):
                        self.take()
                else:
                    while digit(self.peek()) or self.peek() == "_":
                        self.take()
                    if self.peek() == ".":
                        self.take()
                        while digit(self.peek()) or self.peek() == "_":
                            self.take()
                    if self.peek() in ["e", "E"]:
                        self.take()
                        if self.peek() in ["+", "-"]:
                            self.take()
                        while digit(self.peek()) or self.peek() == "_":
                            self.take()
                    if letter(self.peek()):
                        self.error("invalid numeric literal")
                number = self.source[start:self.pos]
                if not valid_number(number):
                    self.error("invalid numeric literal: " + number)
                self.add("number", number, line, column)
            else:
                operator = self.take()
                if operator + self.peek() + self.peek(1) in ["**=", "//=", "<<=", ">>=", "..."]:
                    operator += self.take()
                    operator += self.take()
                elif operator + self.peek() in ["==", "!=", "<=", ">=", "<<", ">>", "**", "//", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=", "->", ":="]:
                    operator += self.take()
                if operator in ["(", "[", "{"]:
                    self.groups.append(operator)
                    if len(self.groups) > 200:
                        self.error("too many nested delimiters")
                elif operator in [")", "]", "}"]:
                    if len(self.groups) == 0:
                        self.error("unmatched closing delimiter")
                    opener = self.groups.pop()
                    if (opener == "(" and operator != ")") or (opener == "[" and operator != "]") or (opener == "{" and operator != "}"):
                        self.error("mismatched closing delimiter")
                elif operator not in ["+", "-", "*", "/", "%", "&", "|", "^", "~", "<", ">", "=", ":", ",", ";", ".", "@", "==", "!=", "<=", ">=", "<<", ">>", "**", "//", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=", "->", ":=", "**=", "//=", "<<=", ">>=", "..."]:
                    self.error("unexpected character: " + operator)
                self.add("op", operator, line, column)
        if len(self.groups) > 0:
            self.error("unclosed delimiter")
        if self.logical:
            self.add("newline", "<newline>", self.line, self.column)
        while len(self.indents) > 1:
            self.indents.pop()
            self.add("dedent", "<dedent>", self.line, self.column)
        self.add("end", "<end>", self.line, self.column)
        return self.tokens


def unescape(text: str, raw: bool, token: Token) -> str:
    if raw:
        return text
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char != "\\":
            out.append(char)
            continue
        if index == len(text):
            token.error("trailing backslash in string")
        escape = text[index]
        index += 1
        if escape in ["\\", "'", '"']:
            out.append(escape)
        elif escape in ["a", "b", "f", "n", "r", "t", "v"]:
            codes = "abfnrtv"
            values = "\a\b\f\n\r\t\v"
            out.append(values[codes.find(escape)])
        elif escape == "\n":
            pass
        elif escape in ["x", "u", "U"]:
            count = 2
            if escape == "u":
                count = 4
            elif escape == "U":
                count = 8
            value = 0
            for unused in range(count):
                if index >= len(text) or hex_digit(text[index]) < 0:
                    token.error("incomplete hexadecimal string escape")
                if value > 1114111 // 16:
                    token.error("Unicode escape out of range")
                value = value * 16 + hex_digit(text[index])
                index += 1
            if value > 1114111 or (value >= 55296 and value <= 57343):
                token.error("string escape is not a Unicode scalar value")
            out.append(chr(value))
        elif escape >= "0" and escape <= "7":
            octal = ord(escape) - 48
            for unused_octal in range(2):
                if index < len(text) and text[index] >= "0" and text[index] <= "7":
                    octal = octal * 8 + ord(text[index]) - 48
                    index += 1
            out.append(chr(octal))
        elif escape == "N":
            if index >= len(text) or text[index] != "{":
                token.error("malformed named Unicode escape")
            close = text.find("}", index + 1)
            if close < 0 or close == index + 1:
                token.error("malformed named Unicode escape")
            out.append(named_unicode(text[index + 1:close], token))
            index = close + 1
        else:
            out.append("\\" + escape)
    return "".join(out)


def binary_operator(text: str) -> str:
    symbols = ["|", "^", "&", "<<", ">>", "+", "-", "*", "@", "/", "//", "%", "**"]
    names = ["BitOr", "BitXor", "BitAnd", "LShift", "RShift", "Add", "Sub", "Mult", "MatMult", "Div", "FloorDiv", "Mod", "Pow"]
    for index in range(len(symbols)):
        if text == symbols[index]:
            return names[index]
    return ""


def binding(text: str) -> i32:
    if text == "or":
        return 1
    if text == "and":
        return 2
    if text in ["<", ">", "<=", ">=", "==", "!=", "in", "is", "not"]:
        return 4
    if text == "|":
        return 5
    if text == "^":
        return 6
    if text == "&":
        return 7
    if text in ["<<", ">>"]:
        return 8
    if text in ["+", "-"]:
        return 9
    if text in ["*", "@", "/", "//", "%"]:
        return 10
    if text == "**":
        return 12
    return -1


def location(node: Node, kind: str) -> Node:
    return Node(kind, "", node.line, node.column)


def constant(value: Node, token: Token) -> Node:
    return token.node("Constant").put("value", value)


def target(node: Node, unpack: bool, deleting: bool = False) -> None:
    if node.kind in ["Name", "Attribute", "Subscript"]:
        return
    if unpack and node.kind in ["Tuple", "List"]:
        for item in node.get("elts").items:
            target(item, True, deleting)
        return
    if unpack and not deleting and node.kind == "Starred":
        target(node.get("value"), False)
        return
    fail("invalid assignment target", node.line, node.column)


class Parser:
    tokens: list[Token]
    pos: i32

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, ahead: i32 = 0) -> Token:
        index = min(self.pos + ahead, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, text: str) -> bool:
        return self.peek().text == text

    def take(self) -> Token:
        token = self.peek()
        if token.kind != "end":
            self.pos += 1
        return token

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.take()
            return True
        return False

    def expect(self, text: str) -> Token:
        token = self.peek()
        if not self.accept(text):
            token.error("expected '" + text + "', got '" + token.text + "'")
        return token

    def name(self) -> str:
        token = self.take()
        if token.kind != "name" or keyword(token.text):
            token.error("expected identifier")
        return token.text

    def end_expression(self) -> bool:
        return self.peek().text in ["<newline>", "<end>", "<dedent>", ";", ":", ")", "]", "}", "=", "in"]

    def module(self) -> Node:
        body: list[Node] = []
        while self.peek().kind != "end":
            body.extend(self.statement())
        return Node("Module").put("body", sequence(body))

    def suite(self) -> Node:
        self.expect(":")
        if not self.accept("<newline>"):
            return sequence(self.simple_line())
        self.expect("<indent>")
        body: list[Node] = []
        while not self.at("<dedent>") and not self.at("<end>"):
            body.extend(self.statement())
        self.expect("<dedent>")
        if len(body) == 0:
            self.peek().error("expected a nonempty suite")
        return sequence(body)

    def statement(self) -> list[Node]:
        decorators: list[Node] = []
        while self.accept("@"):
            decorators.append(self.expression(0))
            self.expect("<newline>")
        async_token = self.peek()
        asynchronous = self.accept("async")
        token = self.peek()
        if self.accept("def"):
            kind = "FunctionDef"
            if asynchronous:
                kind = "AsyncFunctionDef"
                token = async_token
            function_name = self.name()
            self.expect("(")
            args = self.arguments(")", True)
            self.expect(")")
            returns = missing()
            if self.accept("->"):
                returns = self.expression(0)
            body = self.suite()
            return [token.node(kind).put("name", string(function_name)).put("args", args).put("body", body).put("decorator_list", sequence(decorators)).put("returns", returns)]
        if asynchronous and token.text not in ["for", "with"]:
            token.error("expected def, for or with after async")
        if self.accept("class"):
            class_name = self.name()
            bases: list[Node] = []
            keywords: list[Node] = []
            if self.accept("("):
                call = self.call_arguments(token.node("Name"))
                bases = call.get("args").items
                keywords = call.get("keywords").items
            class_body = self.suite()
            return [token.node("ClassDef").put("name", string(class_name)).put("bases", sequence(bases)).put("keywords", sequence(keywords)).put("body", class_body).put("decorator_list", sequence(decorators))]
        if len(decorators) > 0:
            token.error("decorators require a function or class")
        if self.accept("if"):
            return [self.if_statement(token)]
        if self.accept("while"):
            test = self.expression(0)
            while_body = self.suite()
            while_else = sequence([])
            if self.accept("else"):
                while_else = self.suite()
            return [token.node("While").put("test", test).put("body", while_body).put("orelse", while_else)]
        if self.accept("for"):
            for_target = self.expressions(5)
            target(for_target, True)
            self.expect("in")
            iterable = self.expressions(0)
            for_body = self.suite()
            for_else = sequence([])
            if self.accept("else"):
                for_else = self.suite()
            for_kind = "For"
            if asynchronous:
                for_kind = "AsyncFor"
                token = async_token
            return [token.node(for_kind).put("target", for_target).put("iter", iterable).put("body", for_body).put("orelse", for_else)]
        if self.accept("with"):
            items: list[Node] = []
            parenthesized = self.with_parentheses()
            if parenthesized:
                self.expect("(")
            while True:
                context = self.expression(0)
                optional = missing()
                if self.accept("as"):
                    optional = self.expression(5)
                    target(optional, True)
                items.append(Node("withitem").put("context_expr", context).put("optional_vars", optional))
                if not self.accept(","):
                    break
                if parenthesized and self.at(")"):
                    break
            if parenthesized:
                self.expect(")")
            with_body = self.suite()
            with_kind = "With"
            if asynchronous:
                with_kind = "AsyncWith"
                token = async_token
            return [token.node(with_kind).put("items", sequence(items)).put("body", with_body)]
        if self.accept("try"):
            try_body = self.suite()
            handlers: list[Node] = []
            while self.at("except"):
                handler = self.take()
                exception_type = missing()
                exception_name = missing()
                if not self.at(":"):
                    exception_type = self.expression(0)
                    if self.accept("as"):
                        exception_name = string(self.name())
                handler_body = self.suite()
                handlers.append(handler.node("ExceptHandler").put("type", exception_type).put("name", exception_name).put("body", handler_body))
            try_else = sequence([])
            if self.accept("else"):
                if len(handlers) == 0:
                    self.peek().error("try else requires except")
                try_else = self.suite()
            final_body = sequence([])
            if self.accept("finally"):
                final_body = self.suite()
            if len(handlers) == 0 and len(final_body.items) == 0:
                self.peek().error("try requires except or finally")
            return [token.node("Try").put("body", try_body).put("handlers", sequence(handlers)).put("orelse", try_else).put("finalbody", final_body)]
        return self.simple_line()

    def with_parentheses(self) -> bool:
        # A parenthesized with-item list is distinct from an ordinary grouped
        # expression, e.g. with (manager()) as x or with (manager()).field.
        if not self.at("("):
            return False
        depth = 0
        for index in range(self.pos, len(self.tokens)):
            text = self.tokens[index].text
            if text in ["(", "[", "{"]:
                depth += 1
            elif text in [")", "]", "}"]:
                depth -= 1
                if depth == 0:
                    return index + 1 < len(self.tokens) and self.tokens[index + 1].text == ":"
        return False

    def if_statement(self, token: Token) -> Node:
        test = self.expression(0)
        body = self.suite()
        other = sequence([])
        if self.at("elif"):
            elif_token = self.take()
            other = sequence([self.if_statement(elif_token)])
        elif self.accept("else"):
            other = self.suite()
        return token.node("If").put("test", test).put("body", body).put("orelse", other)

    def simple_line(self) -> list[Node]:
        statements: list[Node] = [self.simple()]
        while self.accept(";"):
            if self.at("<newline>"):
                break
            statements.append(self.simple())
        self.expect("<newline>")
        return statements

    def dotted_name(self) -> str:
        text = self.name()
        while self.accept("."):
            text += "." + self.name()
        return text

    def imports(self, dotted: bool, star: bool = False) -> Node:
        aliases: list[Node] = []
        while True:
            token = self.peek()
            text = ""
            if self.accept("*"):
                if not star or len(aliases) > 0:
                    token.error("invalid star import")
                text = "*"
            elif dotted:
                text = self.dotted_name()
            else:
                text = self.name()
            alias = missing()
            if self.accept("as"):
                if text == "*":
                    token.error("star import cannot have an alias")
                alias = string(self.name())
            aliases.append(token.node("alias").put("name", string(text)).put("asname", alias))
            if not self.accept(","):
                break
            if text == "*":
                token.error("star import must stand alone")
            if self.at(")"):
                break
        return sequence(aliases)

    def simple(self) -> Node:
        token = self.peek()
        if self.accept("pass"):
            return token.node("Pass")
        if self.accept("break"):
            return token.node("Break")
        if self.accept("continue"):
            return token.node("Continue")
        if self.accept("return"):
            value = missing()
            if not self.end_expression():
                value = self.expressions(0)
            return token.node("Return").put("value", value)
        if self.accept("raise"):
            exception = missing()
            cause = missing()
            if not self.end_expression():
                exception = self.expression(0)
                if self.accept("from"):
                    cause = self.expression(0)
            return token.node("Raise").put("exc", exception).put("cause", cause)
        if self.accept("assert"):
            test = self.expression(0)
            message = missing()
            if self.accept(","):
                message = self.expression(0)
            return token.node("Assert").put("test", test).put("msg", message)
        if self.accept("del"):
            deletion = self.expressions(0)
            target(deletion, True, True)
            targets: list[Node] = [deletion]
            if deletion.kind == "Tuple":
                targets = deletion.get("elts").items
            return token.node("Delete").put("targets", sequence(targets))
        if token.text in ["global", "nonlocal"]:
            self.take()
            names: list[Node] = [string(self.name())]
            while self.accept(","):
                names.append(string(self.name()))
            declaration = "Global"
            if token.text == "nonlocal":
                declaration = "Nonlocal"
            return token.node(declaration).put("names", sequence(names))
        if self.accept("import"):
            return token.node("Import").put("names", self.imports(True))
        if self.accept("from"):
            level = 0
            while self.at(".") or self.at("..."):
                level += len(self.take().text)
            module = missing()
            if not self.at("import"):
                module = string(self.dotted_name())
            elif level == 0:
                self.peek().error("expected import module")
            self.expect("import")
            parenthesized = self.accept("(")
            imported = self.imports(False, not parenthesized)
            if parenthesized:
                self.expect(")")
            return token.node("ImportFrom").put("module", module).put("names", imported).put("level", scalar(level))
        left = self.expressions(0)
        if self.accept(":"):
            target(left, False)
            annotation = self.expression(0)
            initial = missing()
            if self.accept("="):
                initial = self.expressions(0)
            simple = 0
            if left.kind == "Name" and token.text != "(":
                simple = 1
            return token.node("AnnAssign").put("target", left).put("annotation", annotation).put("value", initial).put("simple", scalar(simple))
        if self.accept("="):
            assigned: list[Node] = [left]
            target(left, True)
            right = self.expressions(0)
            while self.accept("="):
                target(right, True)
                assigned.append(right)
                right = self.expressions(0)
            return token.node("Assign").put("targets", sequence(assigned)).put("value", right)
        operation = self.peek().text
        if operation.endswith("=") and binary_operator(operation[:-1]) != "":
            self.take()
            target(left, False)
            augmented = self.expressions(0)
            return token.node("AugAssign").put("target", left).put("op", Node(binary_operator(operation[:-1]))).put("value", augmented)
        return token.node("Expr").put("value", left)

    def arguments(self, closing: str, annotated: bool) -> Node:
        positional: list[Node] = []
        positional_only: list[Node] = []
        defaults: list[Node] = []
        kwonly: list[Node] = []
        kw_defaults: list[Node] = []
        vararg = missing()
        kwarg = missing()
        keyword_only = False
        slash = False
        bare_star = False
        while not self.at(closing):
            if self.accept("/"):
                if slash or keyword_only or len(positional) == 0:
                    self.peek().error("invalid positional-only separator")
                slash = True
                positional_only = positional
                positional = []
            elif self.accept("**"):
                kwarg = self.argument(annotated)
                self.accept(",")
                if not self.at(closing):
                    self.peek().error("arguments cannot follow **kwargs")
                break
            elif self.accept("*"):
                if keyword_only:
                    self.peek().error("duplicate star parameter")
                keyword_only = True
                if not self.at(","):
                    vararg = self.argument(annotated)
                else:
                    bare_star = True
            else:
                arg = self.argument(annotated)
                default = missing()
                if self.accept("="):
                    default = self.expression(0)
                if keyword_only:
                    kwonly.append(arg)
                    kw_defaults.append(default)
                else:
                    positional.append(arg)
                    if default.kind != "none":
                        defaults.append(default)
                    elif len(defaults) > 0:
                        self.peek().error("non-default argument follows default argument")
            if not self.accept(","):
                break
        if bare_star and len(kwonly) == 0:
            self.peek().error("bare star requires keyword-only parameters")
        return Node("arguments").put("posonlyargs", sequence(positional_only)).put("args", sequence(positional)).put("vararg", vararg).put("kwonlyargs", sequence(kwonly)).put("kw_defaults", sequence(kw_defaults)).put("kwarg", kwarg).put("defaults", sequence(defaults))

    def argument(self, annotated: bool) -> Node:
        token = self.peek()
        name = self.name()
        annotation = missing()
        if annotated and self.accept(":"):
            annotation = self.expression(0)
        return token.node("arg").put("arg", string(name)).put("annotation", annotation)

    def expressions(self, minimum: i32) -> Node:
        token = self.peek()
        first = self.star_expression(minimum)
        if not self.accept(","):
            return first
        if token.text == "yield" and first.kind == "YieldFrom":
            token.error("yield from requires a single expression")
        items: list[Node] = [first]
        while not self.end_expression():
            items.append(self.star_expression(minimum))
            if not self.accept(","):
                break
        return location(first, "Tuple").put("elts", sequence(items))

    def star_expression(self, minimum: i32) -> Node:
        token = self.peek()
        if self.accept("*"):
            return token.node("Starred").put("value", self.expression(5))
        return self.expression(minimum)

    def expression(self, minimum: i32) -> Node:
        token = self.peek()
        left = missing()
        if minimum == 0 and self.accept("lambda"):
            arguments = self.arguments(":", False)
            self.expect(":")
            return token.node("Lambda").put("args", arguments).put("body", self.expression(0))
        if minimum == 0 and self.accept("yield"):
            if self.accept("from"):
                return token.node("YieldFrom").put("value", self.expression(0))
            yielded = missing()
            if not self.end_expression():
                yielded = self.expressions(0)
            return token.node("Yield").put("value", yielded)
        if token.text in ["+", "-", "~", "not"]:
            self.take()
            unary = "UAdd"
            operand_binding = 11
            if token.text == "-":
                unary = "USub"
            elif token.text == "~":
                unary = "Invert"
            elif token.text == "not":
                if minimum > 3:
                    token.error("unexpected not")
                unary = "Not"
                operand_binding = 3
            left = token.node("UnaryOp").put("op", Node(unary)).put("operand", self.expression(operand_binding))
        elif self.accept("await"):
            left = token.node("Await").put("value", self.primary())
        else:
            left = self.primary()
        while binding(self.peek().text) >= minimum:
            operator = self.take()
            power = binding(operator.text)
            if power == 4:
                ops: list[Node] = []
                comparators: list[Node] = []
                while True:
                    comparison = ""
                    if operator.text == "is":
                        comparison = "Is"
                        if self.accept("not"):
                            comparison = "IsNot"
                    elif operator.text == "not":
                        self.expect("in")
                        comparison = "NotIn"
                    else:
                        symbols = ["==", "!=", "<", "<=", ">", ">=", "in"]
                        names = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In"]
                        for index in range(len(symbols)):
                            if operator.text == symbols[index]:
                                comparison = names[index]
                    ops.append(Node(comparison))
                    comparators.append(self.expression(5))
                    if binding(self.peek().text) != 4:
                        break
                    operator = self.take()
                left = location(left, "Compare").put("left", left).put("ops", sequence(ops)).put("comparators", sequence(comparators))
            elif power == 1 or power == 2:
                bool_values: list[Node] = [left, self.expression(power + 1)]
                while self.accept(operator.text):
                    bool_values.append(self.expression(power + 1))
                bool_name = "Or"
                if power == 2:
                    bool_name = "And"
                left = location(left, "BoolOp").put("op", Node(bool_name)).put("values", sequence(bool_values))
            else:
                right_binding = power + 1
                if power == 12:
                    right_binding = 11
                right = self.expression(right_binding)
                left = location(left, "BinOp").put("left", left).put("op", Node(binary_operator(operator.text))).put("right", right)
        if minimum == 0 and self.accept("if"):
            condition = self.expression(1)
            self.expect("else")
            alternative = self.expression(0)
            left = location(left, "IfExp").put("test", condition).put("body", left).put("orelse", alternative)
        return left

    def primary(self) -> Node:
        value = self.atom()
        while True:
            if self.accept("."):
                value = location(value, "Attribute").put("value", value).put("attr", string(self.name()))
            elif self.accept("("):
                value = self.call_arguments(value)
            elif self.accept("["):
                first = self.slice_item()
                subscript = first
                if self.accept(","):
                    slices: list[Node] = [first]
                    while not self.at("]"):
                        slices.append(self.slice_item())
                        if not self.accept(","):
                            break
                    subscript = location(first, "Tuple").put("elts", sequence(slices))
                self.expect("]")
                value = location(value, "Subscript").put("value", value).put("slice", subscript)
            else:
                break
        return value

    def call_arguments(self, function: Node) -> Node:
        args: list[Node] = []
        keywords: list[Node] = []
        unpacked_keyword = False
        while not self.at(")"):
            token = self.peek()
            if self.accept("**"):
                keywords.append(token.node("keyword").put("arg", missing()).put("value", self.expression(0)))
                unpacked_keyword = True
            elif token.kind == "name" and self.peek(1).text == "=":
                name = self.name()
                self.expect("=")
                keywords.append(token.node("keyword").put("arg", string(name)).put("value", self.expression(0)))
            elif self.accept("*"):
                if unpacked_keyword:
                    token.error("iterable unpacking follows keyword unpacking")
                args.append(token.node("Starred").put("value", self.expression(0)))
            else:
                if len(keywords) > 0:
                    token.error("positional argument follows keyword argument")
                args.append(self.expression(0))
            if not self.accept(","):
                break
        self.expect(")")
        return location(function, "Call").put("func", function).put("args", sequence(args)).put("keywords", sequence(keywords))

    def slice_item(self) -> Node:
        token = self.peek()
        lower = missing()
        if not self.at(":"):
            lower = self.star_expression(0)
        if not self.accept(":"):
            return lower
        upper = missing()
        step = missing()
        if self.peek().text not in [":", ",", "]"]:
            upper = self.expression(0)
        if self.accept(":"):
            if self.peek().text not in [",", "]"]:
                step = self.expression(0)
        return token.node("Slice").put("lower", lower).put("upper", upper).put("step", step)

    def atom(self) -> Node:
        token = self.peek()
        if self.accept("("):
            if self.accept(")"):
                return token.node("Tuple").put("elts", sequence([]))
            grouped = self.expressions(0)
            self.expect(")")
            if grouped.kind == "Tuple":
                grouped.line = token.line
                grouped.column = token.column
            return grouped
        if self.accept("["):
            elements: list[Node] = []
            while not self.at("]"):
                elements.append(self.star_expression(0))
                if not self.accept(","):
                    break
            self.expect("]")
            return token.node("List").put("elts", sequence(elements))
        if self.accept("{"):
            keys: list[Node] = []
            values: list[Node] = []
            while not self.at("}"):
                if self.accept("**"):
                    keys.append(missing())
                    values.append(self.expression(0))
                else:
                    keys.append(self.expression(0))
                    self.expect(":")
                    values.append(self.expression(0))
                if not self.accept(","):
                    break
            self.expect("}")
            return token.node("Dict").put("keys", sequence(keys)).put("values", sequence(values))
        if token.kind == "number":
            self.take()
            return constant(Node("Number").put("text", string(token.text)), token)
        if token.kind == "string":
            parts: list[Node] = []
            formatted = False
            while self.peek().kind == "string":
                literal = self.take()
                parsed = parse_string(literal)
                if parsed.kind == "JoinedStr":
                    formatted = True
                    for part in parsed.get("values").items:
                        append_part(parts, part)
                else:
                    append_part(parts, parsed)
            if formatted:
                return token.node("JoinedStr").put("values", sequence(parts))
            if len(parts) == 0:
                return constant(string(""), token)
            return parts[0]
        if self.accept("None"):
            return constant(missing(), token)
        if self.accept("True"):
            return constant(Node("scalar", "true"), token)
        if self.accept("False"):
            return constant(Node("scalar", "false"), token)
        if self.accept("..."):
            return constant(Node("Ellipsis"), token)
        if token.kind == "name" and not keyword(token.text):
            self.take()
            return token.node("Name").put("id", string(token.text))
        token.error("expected expression, got '" + token.text + "'")
        return missing()


def append_part(parts: list[Node], part: Node) -> None:
    if part.kind == "Constant" and part.get("value").kind == "str":
        if part.get("value").text == "":
            return
        if len(parts) > 0 and parts[-1].kind == "Constant":
            previous = parts[-1].get("value")
            previous.text += part.get("value").text
            return
    parts.append(part)


def unescape_fragment(text: str, raw: bool, token: Token) -> str:
    # Unlike an entire string, an f-string text fragment can end in an odd
    # backslash immediately before a replacement field: f'\\{value}'.
    slashes = 0
    index = len(text) - 1
    while index >= 0 and text[index] == "\\":
        slashes += 1
        index -= 1
    if not raw and slashes % 2 == 1:
        return unescape(text[:-1], False, token) + "\\"
    return unescape(text, raw, token)


class FString:
    text: str
    pos: i32
    raw: bool
    token: Token

    def __init__(self, text: str, raw: bool, token: Token) -> None:
        self.text = text
        self.pos = 0
        self.raw = raw
        self.token = token

    def parts(self, spec: bool) -> list[Node]:
        parts: list[Node] = []
        literal = ""
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == "}" and spec:
                self.pos -= 1
                break
            if char in ["{", "}"]:
                if not spec and self.pos < len(self.text) and self.text[self.pos] == char:
                    literal += char
                    self.pos += 1
                elif char == "}":
                    self.token.error("single closing brace in f-string")
                else:
                    append_part(parts, constant(string(unescape_fragment(literal, self.raw, self.token)), self.token))
                    literal = ""
                    field = self.field(parts)
                    parts.append(field)
            else:
                literal += char
                if char == "\\" and self.pos < len(self.text) and self.text[self.pos] not in ["{", "}"]:
                    if not self.raw and self.text[self.pos:self.pos + 2] == "N{":
                        end = self.text.find("}", self.pos + 2)
                        if end < 0:
                            self.token.error("malformed named Unicode escape")
                        literal += self.text[self.pos:end + 1]
                        self.pos = end + 1
                        continue
                    literal += self.text[self.pos]
                    self.pos += 1
        append_part(parts, constant(string(unescape_fragment(literal, self.raw, self.token)), self.token))
        return parts

    def field(self, parts: list[Node]) -> Node:
        start = self.pos
        reader = Lexer(self.text[self.pos:])
        depth = 0
        while reader.peek() != "":
            char = reader.peek()
            if depth == 0 and char in ["!", ":", "}", "="]:
                if char == "=" and reader.peek(1) == "=":
                    reader.take()
                    reader.take()
                    continue
                if char == "!" and reader.peek(1) == "=":
                    reader.take()
                    reader.take()
                    continue
                break
            if char in ["'", '"']:
                reader.quoted("")
            elif char == "#":
                while reader.peek() not in ["", "\n"]:
                    reader.take()
            else:
                reader.take()
                if char in ["(", "[", "{"]:
                    depth += 1
                elif char in [")", "]", "}"]:
                    depth -= 1
                elif char in ["<", ">"] and reader.peek() == "=":
                    reader.take()
        self.pos += reader.pos
        source = self.text[start:self.pos]
        if source.strip() == "":
            self.token.error("empty f-string expression")
        origin = offset_token(self.token, self.text, start)
        expression_lexer = Lexer("(" + source + "\n)")
        expression_lexer.line = origin.line
        expression_lexer.column = origin.column - 1
        wrapped = expression_lexer.scan()
        tokens = wrapped[1:len(wrapped) - 3]
        tokens.extend(wrapped[len(wrapped) - 2:])
        parser = Parser(tokens)
        value = parser.expressions(0)
        parser.expect("<newline>")
        parser.expect("<end>")
        conversion = -1
        debug = False
        if self.pos < len(self.text) and self.text[self.pos] == "=":
            debug = True
            self.pos += 1
            while self.pos < len(self.text) and self.text[self.pos] in [" ", "\t", "\n"]:
                self.pos += 1
            append_part(parts, constant(string(self.text[start:self.pos]), self.token))
        if self.pos < len(self.text) and self.text[self.pos] == "!":
            self.pos += 1
            if self.pos >= len(self.text) or self.text[self.pos] not in ["s", "r", "a"]:
                self.token.error("invalid f-string conversion")
            conversion = ord(self.text[self.pos])
            self.pos += 1
        format_spec = missing()
        if self.pos < len(self.text) and self.text[self.pos] == ":":
            self.pos += 1
            format_spec = self.token.node("JoinedStr").put("values", sequence(self.parts(True)))
        elif debug and conversion == -1:
            conversion = 114
        if self.pos >= len(self.text) or self.text[self.pos] != "}":
            self.token.error("expected closing brace in f-string")
        self.pos += 1
        field_token = offset_token(self.token, self.text, start - 1)
        return field_token.node("FormattedValue").put("value", value).put("conversion", scalar(conversion)).put("format_spec", format_spec)


def parse_string(token: Token) -> Node:
    text = token.text
    index = 0
    while text[index] not in ["'", '"']:
        index += 1
    prefix = text[:index].lower()
    quote = text[index]
    width = 1
    if text[index:index + 3] == quote * 3:
        width = 3
    body = text[index + width:len(text) - width]
    if "f" in prefix:
        body_token = offset_token(token, text, index + width)
        return token.node("JoinedStr").put("values", sequence(FString(body, "r" in prefix, body_token).parts(False)))
    return constant(string(unescape(body, "r" in prefix, token)), token)


def parse(source: str) -> Node:
    return Parser(Lexer(source).scan()).module()


def parse_at(source: str, line: i32) -> Node:
    # Fragments of the fixed runtime retain their original diagnostic locations.
    lexer = Lexer(source)
    lexer.line = line
    return Parser(lexer.scan()).module()


def parse_expression(source: str) -> Node:
    parser = Parser(Lexer(source).scan())
    value = parser.expressions(0)
    parser.expect("<newline>")
    parser.expect("<end>")
    return Node("Expression").put("body", value)




if __name__ == "__main__":
    print(parse(utf8(read_bytes())).json())
