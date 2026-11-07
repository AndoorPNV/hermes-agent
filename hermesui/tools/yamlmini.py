"""Минимальный YAML-парсер под наши манифесты (stdlib-only).

Зачем: в venv Hermes нет PyYAML, а `module.yaml` — контракт каталога. Парсер поддерживает
ровно то подмножество, которое мы используем, и НЕ притворяется полным YAML:

  * комментарии `#` (вне кавычек), пустые строки;
  * блочные словари по отступу, блочные списки `- item`;
  * элементы списка-словари (`- kind: desktop` + продолжение с бо́льшим отступом);
  * flow-коллекции `[a, b]`, `{k: v}` (рекурсивно);
  * скаляры: строки в кавычках (одинарных/двойных), bool, int, float, null/~/пусто;
  * многострочные строки (`|`, `>`) — не поддерживаются: на них бросается YAMLUnsupported,
    чтобы манифест не «поехал» молча.

Возвращает `(data, lines)`, где `lines: dict[tuple, int]` — номер строки (1-based) для пути
внутри данных. Валидатор каталога показывает эти номера в ошибках.
"""

from __future__ import annotations

from typing import Any


class YAMLError(ValueError):
    """Ошибка разбора с номером строки."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(f"строка {line}: {message}" if line else message)
        self.line = line


class YAMLUnsupported(YAMLError):
    pass


def _strip_comment(text: str) -> str:
    """Убирает `#...`, не трогая содержимое кавычек."""
    out = []
    quote = ""
    prev = ""
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote and prev != "\\":
                quote = ""
        else:
            if ch in "'\"":
                quote = ch
                out.append(ch)
            elif ch == "#" and (not out or out[-1] in " \t"):
                break
            else:
                out.append(ch)
        prev = ch
    return "".join(out).rstrip()


class _Line:
    __slots__ = ("indent", "text", "no")

    def __init__(self, indent: int, text: str, no: int) -> None:
        self.indent = indent
        self.text = text
        self.no = no


def _is_seq_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _split_key(text: str, no: int) -> tuple[str, str]:
    quote = ""
    for pos, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch == ":":
            return text[:pos].strip(), text[pos + 1:].strip()
    raise YAMLError(f"ожидалось `ключ: значение`, получено {text!r}", no)


def _scalar(raw: str, no: int) -> Any:
    token = raw.strip()
    if token == "":
        return None
    if token[0] in "[{":
        value, rest = _flow(token, no)
        if rest.strip():
            raise YAMLError(f"лишний текст после flow-коллекции: {rest!r}", no)
        return value
    if token in ("|", ">", "|-", ">-", "|+", ">+"):
        raise YAMLUnsupported("многострочные строки (`|`, `>`) не поддерживаются", no)
    if token[0] in "'\"" and not (len(token) >= 2 and token[-1] == token[0]):
        raise YAMLUnsupported(
            f"строка в кавычках не закрыта на этой строке ({token[:24]!r}…); "
            "многострочные значения не поддерживаются", no)
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        inner = token[1:-1]
        if token[0] == '"':
            return _unescape_double(inner)
        return inner.replace("''", "'")
    lowered = token.lower()
    if lowered in ("null", "~"):
        return None
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _flow(text: str, no: int) -> tuple[Any, str]:
    """Разбирает `[...]` / `{...}` с начала строки; возвращает (значение, остаток)."""
    value, end = _flow_value(text, 0, no)
    return value, text[end:]


def _read_token(text: str, i: int, stops: str, no: int) -> tuple[str, int]:
    """Читает скаляр до одного из *stops* вне кавычек. Возвращает (сырой токен, позицию)."""
    start = i
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in stops:
            break
        i += 1
    if quote:
        raise YAMLError("незакрытая кавычка во flow-коллекции", no)
    return text[start:i].strip(), i


def _flow_value(text: str, i: int, no: int) -> tuple[Any, int]:
    opener = text[i]
    closer = "]" if opener == "[" else "}"
    i += 1
    items: list[Any] = []
    mapping: dict[str, Any] = {}
    while True:
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text):
            raise YAMLError(f"незакрытая flow-коллекция (нет {closer!r})", no)
        ch = text[i]
        if ch == closer:
            return (items if opener == "[" else mapping), i + 1
        if ch == ",":
            i += 1
            continue
        if opener == "[":
            if ch in "[{":
                value, i = _flow_value(text, i, no)
            else:
                token, i = _read_token(text, i, ",]", no)
                value = _scalar(token, no) if token else None
            items.append(value)
            continue
        key, i = _read_token(text, i, ":", no)
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text) and text[i] == ":":
            i += 1
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text) and text[i] in "[{":
            value, i = _flow_value(text, i, no)
        else:
            token, i = _read_token(text, i, ",}", no)
            value = _scalar(token, no) if token else None
        mapping[_unquote(key)] = value
    raise YAMLError(f"незакрытая flow-коллекция (нет {closer!r})", no)


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0"}


def _unescape_double(inner: str) -> str:
    """Раскрывает \\n, \\t, \\", \\\\ и т.п., не трогая не-ASCII.

    `unicode_escape` здесь неприменим: он декодирует байты utf-8 как latin-1 и превращает
    «Общий» в мусор. Для юникода оставляем символ как есть.
    """
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch != "\\" or i + 1 >= len(inner):
            out.append(ch)
            i += 1
            continue
        nxt = inner[i + 1]
        if nxt == "u" and i + 5 < len(inner):
            try:
                out.append(chr(int(inner[i + 2:i + 6], 16)))
                i += 6
                continue
            except ValueError:
                pass
        out.append(_ESCAPES.get(nxt, "\\" + nxt))
        i += 2
    return "".join(out)


def _unquote(key: str) -> str:
    key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
        return key[1:-1]
    return key


def parse(text: str) -> tuple[Any, dict[tuple, int]]:
    lines: list[_Line] = []
    for no, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.strip() in ("---", "..."):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YAMLError("табуляция в отступе недопустима (только пробелы)", no)
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append(_Line(indent, stripped.strip(), no))

    marks: dict[tuple, int] = {}
    if not lines:
        return {}, marks
    value, idx = _parse_block(lines, 0, lines[0].indent, (), marks)
    if idx != len(lines):
        raise YAMLError(f"лишние строки начиная с {lines[idx].text!r}", lines[idx].no)
    return value, marks


def _parse_block(lines: list[_Line], i: int, indent: int, path: tuple,
                 marks: dict[tuple, int]) -> tuple[Any, int]:
    if _is_seq_item(lines[i].text):
        return _parse_seq(lines, i, indent, path, marks)
    return _parse_map(lines, i, indent, path, marks)


def _parse_seq(lines: list[_Line], i: int, indent: int, path: tuple,
               marks: dict[tuple, int]) -> tuple[list, int]:
    out: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and _is_seq_item(lines[i].text):
        line = lines[i]
        item_path = path + (len(out),)
        marks[item_path] = line.no
        body = line.text[1:].strip()
        if not body:
            i += 1
            if i < len(lines) and lines[i].indent > indent:
                value, i = _parse_block(lines, i, lines[i].indent, item_path, marks)
            else:
                value = None
            out.append(value)
            continue
        if not _looks_like_mapping(body):
            out.append(_scalar(body, line.no))
            i += 1
            continue
        # элемент списка — словарь: первая пара в строке `- key: value`, продолжение ниже
        item: dict[str, Any] = {}
        key, rest = _split_key(body, line.no)
        key = _unquote(key)
        marks[item_path + (key,)] = line.no
        i += 1
        if rest:
            item[key] = _scalar(rest, line.no)
        else:
            if i < len(lines) and lines[i].indent > indent:
                item[key], i = _parse_block(lines, i, lines[i].indent, item_path + (key,), marks)
            else:
                item[key] = None
        while i < len(lines) and lines[i].indent > indent:
            sub_indent = lines[i].indent
            if _is_seq_item(lines[i].text):
                break
            extra, i = _parse_map(lines, i, sub_indent, item_path, marks)
            item.update(extra)
        out.append(item)
    return out, i


def _looks_like_mapping(text: str) -> bool:
    """`ключ: значение` только если после двоеточия пробел или конец строки.

    Иначе Windows-путь `F:\\06_AI\\...` в списке превращается в словарь {'F': '\\06_AI\\...'}.
    """
    quote = ""
    for idx, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch == ":":
            return idx + 1 == len(text) or text[idx + 1] in " \t"
        elif ch in "[{":
            return False
    return False


def _parse_map(lines: list[_Line], i: int, indent: int, path: tuple,
               marks: dict[tuple, int]) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent and not _is_seq_item(lines[i].text):
        line = lines[i]
        key, rest = _split_key(line.text, line.no)
        key = _unquote(key)
        key_path = path + (key,)
        if key in out:
            raise YAMLError(f"дубликат ключа {key!r}", line.no)
        marks[key_path] = line.no
        i += 1
        if rest:
            out[key] = _scalar(rest, line.no)
            continue
        if i < len(lines) and lines[i].indent > indent:
            out[key], i = _parse_block(lines, i, lines[i].indent, key_path, marks)
        elif i < len(lines) and lines[i].indent == indent and _is_seq_item(lines[i].text):
            out[key], i = _parse_seq(lines, i, indent, key_path, marks)
        else:
            out[key] = None
    return out, i


def load(path) -> tuple[Any, dict[tuple, int]]:
    from pathlib import Path

    return parse(Path(path).read_text(encoding="utf-8"))
