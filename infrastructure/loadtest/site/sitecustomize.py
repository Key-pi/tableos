"""Load test only: point every aiogram session at the fake Telegram API.

Python imports ``sitecustomize`` at start-up when this folder is on
``PYTHONPATH`` (``env.sh`` puts it there). With ``FAKE_TG`` set, the bot, the
workers and anything else using aiogram talk to ``loadgen.py`` instead of
api.telegram.org. Never on a production ``PYTHONPATH``.
"""

import os

if os.environ.get("FAKE_TG"):
    from aiogram.client.session import base as _base
    from aiogram.client.telegram import TelegramAPIServer as _Server

    _server = _Server.from_base(os.environ["FAKE_TG"])
    _orig_init = _base.BaseSession.__init__

    def _init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.api = _server

    _base.BaseSession.__init__ = _init
