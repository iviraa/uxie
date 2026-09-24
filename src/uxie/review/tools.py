"""Tool specs (Converse toolSpec format) and dispatch onto a Workspace."""

from __future__ import annotations

from typing import Any

from uxie.review.workspace import Workspace

SEVERITIES = ["high", "medium", "low"]
CATEGORIES = ["bug", "security", "performance", "concurrency", "error-handling"]

SUBMIT_REVIEW: dict[str, Any] = {
    "toolSpec": {
        "name": "submit_review",
        "description": "Submit the final review. Call exactly once.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "1-3 sentences on what the PR does and overall risk.",
                    },
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "line": {"type": "integer"},
                                "severity": {"type": "string", "enum": SEVERITIES},
                                "category": {"type": "string", "enum": CATEGORIES},
                                "title": {"type": "string"},
                                "explanation": {"type": "string"},
                                "suggestion": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": [
                                "path",
                                "line",
                                "severity",
                                "category",
                                "title",
                                "explanation",
                                "confidence",
                            ],
                        },
                    },
                },
                "required": ["summary", "findings"],
            }
        },
    }
}

READ_FILE: dict[str, Any] = {
    "toolSpec": {
        "name": "read_file",
        "description": "Read a file at the PR head commit. Returns numbered lines (max 400).",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            }
        },
    }
}

LIST_DIR: dict[str, Any] = {
    "toolSpec": {
        "name": "list_dir",
        "description": "List a directory at the PR head commit.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            }
        },
    }
}

SEARCH_CODE: dict[str, Any] = {
    "toolSpec": {
        "name": "search_code",
        "description": "Regex search over the repo at the PR head. Returns path:line: text.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex"},
                    "glob": {"type": "string", "description": "optional, e.g. *.py"},
                },
                "required": ["pattern"],
            }
        },
    }
}

CONTEXT_TOOLS = [READ_FILE, LIST_DIR, SEARCH_CODE]


def dispatch(ws: Workspace, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "read_file":
            return ws.read_file(
                str(args["path"]),
                int(args.get("start_line") or 1),
                int(args["end_line"]) if args.get("end_line") else None,
            )
        if name == "list_dir":
            return ws.list_dir(str(args.get("path") or ""))
        if name == "search_code":
            return ws.search(str(args["pattern"]), args.get("glob") or None)
    except (KeyError, ValueError, TypeError) as e:
        return f"error: {e}"
    return f"error: unknown tool {name}"
