"""Render a minimal, non-secret billing inventory annotation for GitHub checks.

This reports observed API status and exact booleans only. Neither subscription
counts nor an unavailable spend-policy API proves a free plan or hard stop.
"""
from __future__ import annotations


def build_annotation(summary: dict) -> str:
    fields = []
    for key in ("spend_policy_http_status", "subscriptions_http_status"):
        value = summary.get(key)
        if type(value) is not int or not 100 <= value <= 599:
            raise ValueError(f"invalid {key}")
        fields.append(f"{key}={value}")
    for key in ("enabled_workers_usage_block", "enabled_durable_objects_usage_block"):
        value = summary.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(f"invalid {key}")
        if summary["spend_policy_http_status"] != 200:
            value = None
        fields.append(f"{key}={'unknown' if value is None else str(value).lower()}")
    return ("::notice title=Billing inventory (read-only)::" + "; ".join(fields)
            + "; account plan, billing hard stop and zero cost are not established")
