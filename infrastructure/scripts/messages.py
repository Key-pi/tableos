"""Каталог текстов: сбор из кода, проверка и компиляция без GNU gettext.

Тексты в коде — по-русски, в ``_()`` / ``gettext_lazy()`` / ``N_()`` / ``variants()``
(``core/i18n.py``). Переводы лежат в ``locale/<язык>/LC_MESSAGES/``:

- ``django.po`` — тексты кода (украинский и английский; русский — сам текст);
- ``theme.po`` — строки темы Admin (django-unfold), их msgid английские.

Команды:

    python infrastructure/scripts/messages.py update    # новые тексты в каталоги
    python infrastructure/scripts/messages.py check     # что не переведено
    python infrastructure/scripts/messages.py compile   # .po → django.mo

``update`` добавляет новые тексты с пустым переводом и убирает те, которых
в коде больше нет; готовые переводы сохраняет. Тест
``core/tests_i18n_catalog.py`` требует, чтобы ``check`` был пуст.
"""

from __future__ import annotations

import ast
import pathlib
import re
import string
import struct
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("apps", "bot", "core")
CODE_CATALOG = "django.po"
TARGET_LANGUAGES = ("uk", "en")
LANGUAGES = ("uk", "ru", "en")
# Function name -> index of the msgid argument.
MARKERS = {
    "_": 0,
    "gettext": 0,
    "gettext_lazy": 0,
    "_lazy": 0,
    "variants": 0,
    "N_": 0,
    "own_text": 0,
    "gettext_in": 1,
}
PLURAL_FORMS = {
    "uk": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
    "(n%100<10 || n%100>=20) ? 1 : 2);",
    "ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
    "(n%100<10 || n%100>=20) ? 1 : 2);",
    "en": "nplurals=2; plural=(n != 1);",
}
TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)[^>]*>")


@dataclass
class Message:
    msgid: str
    msgstr: str = ""
    places: list[str] = field(default_factory=list)


# --- reading the code ------------------------------------------------------


def _is_source(path: pathlib.Path) -> bool:
    parts = path.relative_to(ROOT).parts
    name = path.name
    return not (
        "migrations" in parts
        or "management" in parts
        or name.startswith("test")
        or name.startswith("tests")
    )


def _marker(node: ast.Call) -> int | None:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return MARKERS.get(name)


def extract(root: pathlib.Path = ROOT) -> dict[str, list[str]]:
    """msgid -> places ("path:line") in code order."""

    found: dict[str, list[str]] = {}
    for directory in SOURCE_DIRS:
        for path in sorted((root / directory).rglob("*.py")):
            if not _is_source(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                index = _marker(node)
                if index is None or len(node.args) <= index:
                    continue
                arg = node.args[index]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                    place = f"{path.relative_to(root)}:{node.lineno}"
                    found.setdefault(arg.value, []).append(place)
    for places in found.values():
        places.sort(key=_place_key)
    return dict(sorted(found.items(), key=lambda item: _place_key(item[1][0])))


def _place_key(place: str) -> tuple[str, int]:
    path, line = place.rsplit(":", 1)
    return path, int(line)


# --- .po files ---------------------------------------------------------------


def _unquote(token: str) -> str:
    return ast.literal_eval(token)


def parse_po(text: str) -> dict[str, str]:
    """msgid -> msgstr; the header (msgid "") is skipped."""

    messages: dict[str, str] = {}
    current: str | None = None
    parts: dict[str, list[str]] = {}

    def flush() -> None:
        if "msgid" in parts:
            msgid = "".join(parts["msgid"])
            if msgid:
                messages[msgid] = "".join(parts.get("msgstr", []))
        parts.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            flush()
            current = "msgid"
            parts[current] = [_unquote(line[6:])]
        elif line.startswith("msgstr "):
            current = "msgstr"
            parts[current] = [_unquote(line[7:])]
        elif line.startswith('"') and current:
            parts[current].append(_unquote(line))
    flush()
    return messages


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    lines = escaped.split("\n")
    if len(lines) == 1:
        return f'"{escaped}"'
    chunks = [f"{line}\\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    return '""\n' + "\n".join(f'"{chunk}"' for chunk in chunks)


def render_po(language: str, messages: list[Message]) -> str:
    out = [
        f"# Тексты Botera Shop: {language}. Обновлять: "
        "python infrastructure/scripts/messages.py update",
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        f'"Language: {language}\\n"',
        "",
    ]
    for message in messages:
        paths = dict.fromkeys(place.rsplit(":", 1)[0] for place in message.places)
        out.append("#: " + " ".join(paths))
        out.append(f"msgid {_quote(message.msgid)}")
        out.append(f"msgstr {_quote(message.msgstr)}")
        out.append("")
    return "\n".join(out)


def catalog_path(language: str, name: str = CODE_CATALOG, root: pathlib.Path = ROOT):
    return root / "locale" / language / "LC_MESSAGES" / name


def read_catalog(language: str, name: str = CODE_CATALOG, root: pathlib.Path = ROOT):
    path = catalog_path(language, name, root)
    return parse_po(path.read_text(encoding="utf-8")) if path.exists() else {}


# --- checks ------------------------------------------------------------------


def _fields(text: str) -> list[str]:
    try:
        return sorted(
            name + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "")
            for _literal, name, spec, conv in string.Formatter().parse(text)
            if name is not None
        )
    except ValueError:
        return ["<неверные скобки>"]


def _tags(text: str) -> list[str]:
    return sorted(TAG.findall(text))


def problems(root: pathlib.Path = ROOT) -> list[str]:
    found = extract(root)
    issues: list[str] = []
    for language in TARGET_LANGUAGES:
        catalog = read_catalog(language, root=root)
        for msgid, places in found.items():
            translation = catalog.get(msgid, "")
            where = places[0]
            if not translation:
                issues.append(f"{language}: нет перевода ({where}): {msgid!r}")
                continue
            if _fields(translation) != _fields(msgid):
                issues.append(f"{language}: подстановки не совпадают ({where}): {msgid!r}")
            if _tags(translation) != _tags(msgid):
                issues.append(f"{language}: теги не совпадают ({where}): {msgid!r}")
            if translation.endswith("\n") != msgid.endswith("\n"):
                issues.append(f"{language}: перевод строки в конце ({where}): {msgid!r}")
        for msgid in catalog.keys() - found.keys():
            issues.append(f"{language}: текста больше нет в коде: {msgid!r}")
    for language in LANGUAGES:
        mo = catalog_path(language, "django.mo", root)
        if compiled_messages(language, root) != (read_mo(mo) if mo.exists() else None):
            issues.append(f"{language}: django.mo устарел — messages.py compile")
    return issues


# --- .mo ---------------------------------------------------------------------


def compiled_messages(language: str, root: pathlib.Path = ROOT) -> dict[str, str]:
    messages: dict[str, str] = {}
    for po in sorted((root / "locale" / language / "LC_MESSAGES").glob("*.po")):
        messages.update(
            {k: v for k, v in parse_po(po.read_text(encoding="utf-8")).items() if v}
        )
    messages[""] = (
        "Content-Type: text/plain; charset=UTF-8\n"
        f"Language: {language}\n"
        f"Plural-Forms: {PLURAL_FORMS[language]}\n"
    )
    return messages


def write_mo(messages: dict[str, str], path: pathlib.Path) -> None:
    keys = sorted(messages)
    ids = b""
    strs = b""
    offsets = []
    for key in keys:
        kid, kstr = key.encode(), messages[key].encode()
        offsets.append((len(ids), len(kid), len(strs), len(kstr)))
        ids += kid + b"\0"
        strs += kstr + b"\0"
    n = len(keys)
    key_start = 7 * 4 + 16 * n
    value_start = key_start + len(ids)
    table = []
    vtable = []
    for id_offset, id_length, str_offset, str_length in offsets:
        table += [id_length, id_offset + key_start]
        vtable += [str_length, str_offset + value_start]
    header = struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    path.write_bytes(
        header
        + struct.pack(f"{len(table)}i", *table)
        + struct.pack(f"{len(vtable)}i", *vtable)
        + ids
        + strs
    )


def read_mo(path: pathlib.Path) -> dict[str, str]:
    data = path.read_bytes()
    _magic, _rev, n, key_table, value_table, _s, _h = struct.unpack("Iiiiiii", data[:28])
    messages = {}
    for index in range(n):
        klen, koff = struct.unpack("ii", data[key_table + 8 * index : key_table + 8 * index + 8])
        vlen, voff = struct.unpack(
            "ii", data[value_table + 8 * index : value_table + 8 * index + 8]
        )
        messages[data[koff : koff + klen].decode()] = data[voff : voff + vlen].decode()
    return messages


# --- commands ----------------------------------------------------------------


def update(root: pathlib.Path = ROOT) -> None:
    found = extract(root)
    for language in TARGET_LANGUAGES:
        old = read_catalog(language, root=root)
        messages = [
            Message(msgid, old.get(msgid, ""), places) for msgid, places in found.items()
        ]
        path = catalog_path(language, root=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_po(language, messages), encoding="utf-8")
        missing = sum(1 for message in messages if not message.msgstr)
        gone = len(old.keys() - found.keys())
        print(f"{path.relative_to(root)}: {len(messages)} текстов, без перевода {missing}, "
              f"убрано {gone}")


def compile_all(root: pathlib.Path = ROOT) -> None:
    for language in LANGUAGES:
        directory = root / "locale" / language / "LC_MESSAGES"
        if not directory.exists():
            continue
        messages = compiled_messages(language, root)
        write_mo(messages, directory / "django.mo")
        print(f"{directory.relative_to(root)}/django.mo: {len(messages) - 1}")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "update":
        update()
    elif command == "compile":
        compile_all()
    elif command == "check":
        issues = problems()
        print("\n".join(issues) or "Всё переведено.")
        return 1 if issues else 0
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
