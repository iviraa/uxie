"""Loaded at interpreter start: add the dependency env like a real site-packages.

site.addsitedir appends after the standard library (PYTHONPATH would go before it, letting
old backports such as `dataclasses` shadow stdlib modules) and processes .pth files.
"""

import os
import site

_deps = os.environ.get("UXIE_DEPS")
if _deps and os.path.isdir(_deps):
    site.addsitedir(_deps)
