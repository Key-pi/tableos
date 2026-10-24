"""Compile locale/*/LC_MESSAGES/django.po into .mo without GNU gettext.

The Admin theme (django-unfold) ships no Russian translation; ours is in
locale/ru/LC_MESSAGES/django.po. Run after editing it:

    python infrastructure/scripts/compile_messages.py

Supports what the file uses: msgctxt-free single-line msgid/msgstr pairs.
"""

from __future__ import annotations

import ast
import pathlib
import struct

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADER = (
    "Content-Type: text/plain; charset=UTF-8\n"
    "Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && "
    "n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"
)


def parse_po(text: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    msgid = msgstr = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            msgid = ast.literal_eval(line[6:])
        elif line.startswith("msgstr ") and msgid is not None:
            msgstr = ast.literal_eval(line[7:])
            if msgstr or msgid == "":
                messages[msgid] = msgstr
            msgid = msgstr = None
        elif line.startswith('"') and msgid == "" and msgstr is None:
            continue
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


def main() -> None:
    for po in ROOT.glob("locale/*/LC_MESSAGES/*.po"):
        messages = parse_po(po.read_text(encoding="utf-8"))
        # The header (charset, Russian plural forms) is fixed here; the .po
        # header lines are documentation only.
        messages[""] = HEADER
        write_mo(messages, po.with_suffix(".mo"))
        print(f"{po.relative_to(ROOT)}: {len(messages) - 1} messages")


if __name__ == "__main__":
    main()
