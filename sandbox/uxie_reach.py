"""pytest plugin: record which lines of one target file execute during the test run.

Only frames from the target file are traced line-by-line, so the rest of the code
runs at near full speed. Results go to /tmp/reach.json for run_test.py.
"""

from __future__ import annotations

import json
import os
import sys
import threading

OUT = "/tmp/reach.json"
_target = os.path.realpath(os.path.join(os.getcwd(), os.environ.get("UXIE_TARGET_FILE", "")))
_lines: set[int] = set()
_seen: dict[str, bool] = {}


def _local(frame, event, arg):  # noqa: ANN001, ANN202
    if event == "line":
        _lines.add(frame.f_lineno)
    return _local


def _global(frame, event, arg):  # noqa: ANN001, ANN202
    name = frame.f_code.co_filename
    hit = _seen.get(name)
    if hit is None:
        hit = _seen[name] = os.path.realpath(name) == _target
    if hit:
        _lines.add(frame.f_lineno)  # the def/first line of the frame counts too
        return _local
    return None


def pytest_configure(config):  # noqa: ANN001, ANN201
    sys.settrace(_global)
    threading.settrace(_global)


def pytest_unconfigure(config):  # noqa: ANN001, ANN201
    sys.settrace(None)
    with open(OUT, "w") as fh:
        json.dump(sorted(_lines)[:5000], fh)
