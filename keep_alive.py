"""
Minimal HTTP server so hosts like Render (free Web Service tier) see an open
port and consider the service healthy. Discord bots don't need to serve HTTP
themselves, but Render's free web services require *something* listening on
$PORT — this satisfies that with almost no overhead, running in a background
thread alongside the actual bot.

If you're hosting somewhere that doesn't need this (a VPS, a background
worker plan, your own machine), you can simply not call keep_alive() and
nothing changes.
"""

import os
import threading

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "LIB Bypass Bot is running."


def _run():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
