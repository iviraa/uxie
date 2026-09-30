"""Loaded at interpreter start: add the dependency env like a real site-packages.

site.addsitedir appends after the standard library (PYTHONPATH would go before it, letting
old backports such as `dataclasses` shadow stdlib modules) and processes .pth files.
"""

import os
import site

_deps = os.environ.get("UXIE_DEPS")
if _deps and os.path.isdir(_deps):
    site.addsitedir(_deps)

try:  # property-based tests must give the same answer on every run, or verdicts could flip
    from hypothesis import HealthCheck, settings

    settings.register_profile(
        "uxie",
        derandomize=True,
        database=None,
        deadline=None,
        max_examples=200,
        suppress_health_check=list(HealthCheck),
    )
    settings.load_profile("uxie")
except ImportError:
    pass
