"""Generate deploy/grafana/uxie-dashboard.json (dashboard as code).

Lambda containers are short-lived, so each one reports its own counter series that starts at
zero and gets only a few samples. increase()/rate() need 2+ samples per series and come back
empty, so counters are summed as max-per-series over the window instead.
"""

from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "grafanacloud-prom"}
TEMPO = {"type": "tempo", "uid": "grafanacloud-traces"}


def total(metric: str, by: str = "", window: str = "$__range", sel: str = "") -> str:
    """Events in the window, correct for counters that restart with every container."""
    group = f" by ({by})" if by else ""
    return f"sum{group} (max_over_time({metric}{sel}[{window}]))"


def quantile(q: float, metric: str, by: str = "le", window: str = "1h") -> str:
    return f"histogram_quantile({q}, sum by ({by}) (max_over_time({metric}_bucket[{window}])))"


PANELS: list[dict] = [
    # (title, type, [(expr, legend)], width, height, unit, instant)
    {
        "t": "Reviews",
        "k": "stat",
        "q": [(total("uxie_reviews_total", sel='{status="done"}'), "")],
        "w": 6,
        "h": 4,
    },
    {
        "t": "Findings posted",
        "k": "stat",
        "q": [(total("uxie_findings_total"), "")],
        "w": 6,
        "h": 4,
    },
    {
        "t": "Confirmed by a test",
        "k": "stat",
        "q": [(total("uxie_verdicts_total", sel='{verdict=~"confirmed|likely"}'), "")],
        "w": 6,
        "h": 4,
    },
    {
        "t": "LLM cost",
        "k": "stat",
        "q": [(total("uxie_llm_cost_USD_total"), "")],
        "w": 6,
        "h": 4,
        "u": "currencyUSD",
        "dec": 3,
    },
    {
        "t": "Reviews per hour (rolling)",
        "k": "timeseries",
        "q": [(total("uxie_reviews_total", "status", "1h"), "{{status}}")],
        "range": True,
    },
    {
        "t": "Verdicts",
        "k": "piechart",
        "q": [(total("uxie_verdicts_total", "verdict"), "{{verdict}}")],
    },
    {
        "t": "Review latency",
        "k": "timeseries",
        "u": "s",
        "range": True,
        "q": [
            (quantile(0.5, "uxie_review_duration_seconds"), "p50"),
            (quantile(0.95, "uxie_review_duration_seconds"), "p95"),
        ],
    },
    {
        "t": "Proof latency p95 by verdict",
        "k": "timeseries",
        "u": "s",
        "range": True,
        "q": [(quantile(0.95, "uxie_proof_duration_seconds", "le, verdict"), "{{verdict}}")],
    },
    {
        "t": "Tokens by model",
        "k": "barchart",
        "q": [(total("uxie_llm_tokens_total", "model, kind"), "{{model}} {{kind}}")],
    },
    {
        "t": "Findings by severity",
        "k": "barchart",
        "q": [(total("uxie_findings_total", "severity"), "{{severity}}")],
    },
]


def build() -> dict:
    panels, x, y, row_h = [], 0, 0, 0
    for i, p in enumerate(PANELS, 1):
        w, h = p.get("w", 12), p.get("h", 8)
        if x + w > 24:
            x, y, row_h = 0, y + row_h, 0
        targets = [
            {
                "datasource": PROM,
                "refId": chr(65 + j),
                "expr": expr,
                "legendFormat": legend,
                "instant": not p.get("range"),
                "range": bool(p.get("range")),
            }
            for j, (expr, legend) in enumerate(p["q"])
        ]
        defaults: dict = {"unit": p["u"]} if p.get("u") else {}
        if "dec" in p:
            defaults["decimals"] = p["dec"]
        panels.append(
            {
                "id": i,
                "title": p["t"],
                "type": p["k"],
                "datasource": PROM,
                "targets": targets,
                "gridPos": {"x": x, "y": y, "w": w, "h": h},
                "fieldConfig": {"defaults": defaults, "overrides": []},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"]}}
                if p["k"] != "timeseries"
                else {},
            }
        )
        x, row_h = x + w, max(row_h, h)
    panels.append(
        {
            "id": len(panels) + 1,
            "title": "Recent traces",
            "type": "table",
            "datasource": TEMPO,
            "gridPos": {"x": 0, "y": y + row_h, "w": 24, "h": 9},
            "targets": [
                {
                    "datasource": TEMPO,
                    "refId": "A",
                    "queryType": "traceql",
                    "query": '{ resource.service.namespace = "uxie" }',
                    "limit": 20,
                }
            ],
            "fieldConfig": {"defaults": {}, "overrides": []},
            "options": {},
        }
    )
    return {
        "uid": "uxie-overview",
        "title": "uxie",
        "tags": ["uxie"],
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now"},
        "refresh": "1m",
        "panels": panels,
        "templating": {"list": []},
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("uxie-dashboard.json")
    out.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {out} ({len(PANELS) + 1} panels)")
