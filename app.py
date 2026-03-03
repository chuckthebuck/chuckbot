#!/usr/bin/env python3
"""Toolforge web entrypoint.

For Toolforge Python webservice, this module exposes `app` at top-level so
uWSGI can import it from `~/www/python/src/app.py`.
"""

from toolforge_queue_api import APP as app


if __name__ == "__main__":
    # Local development fallback only.
    app.run(host="0.0.0.0", port=8000)
