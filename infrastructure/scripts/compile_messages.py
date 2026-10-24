"""Compile locale/*/LC_MESSAGES/*.po into django.mo without GNU gettext.

Kept for old instructions; the tool is messages.py (update, check, compile):

    python infrastructure/scripts/compile_messages.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from messages import compile_all  # noqa: E402

if __name__ == "__main__":
    compile_all()
