#!/usr/bin/env python3
"""Simulate a Sentry NullPointerException outage storm.

Fires 5 identical webhook payloads at POST /v1/webhooks/sentry.
Expected behaviour:
  • Request 1 → 202 Accepted  (status: "queued")
  • Requests 2-5 → 200 OK     (status: "deduplicated", $0 LLM cost)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

import httpx

BASE_URL = "http://localhost:8000"
WEBHOOK_SECRET = "change-me-to-a-real-secret"
ENDPOINT = f"{BASE_URL}/v1/webhooks/sentry"

# Realistic Sentry webhook payload — NullPointerException from OrderController
SENTRY_PAYLOAD = {
    "action": "created",
    "data": {
        "event": {
            "event_id": "abc123deadbeef",
            "title": "NullPointerException: discountCode.length()",
            "culprit": "com.victim.controller.OrderController.createOrder",
            "timestamp": 1700000000.0,
            "exception": {
                "values": [
                    {
                        "type": "NullPointerException",
                        "value": "Cannot invoke String.length() because discountCode is null",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "SpringApplication.java",
                                    "lineno": 1352,
                                    "function": "run",
                                },
                                {
                                    "filename": "DispatcherServlet.java",
                                    "lineno": 1067,
                                    "function": "doDispatch",
                                },
                                {
                                    "filename": "OrderController.java",
                                    "lineno": 34,
                                    "function": "createOrder",
                                },
                            ],
                        },
                    }
                ]
            },
        }
    },
}


def sign(body: bytes) -> str:
    """Generate HMAC-SHA256 hex digest."""
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def main() -> None:
    print("=" * 70)
    print("  Sentinel-RCA · Outage Storm Simulation")
    print("  Firing 5 identical NullPointerException webhooks")
    print("=" * 70)
    print()

    body = json.dumps(SENTRY_PAYLOAD).encode()
    sig = sign(body)

    queued = 0
    deduped = 0

    for i in range(1, 6):
        t0 = time.perf_counter_ns()
        resp = httpx.post(
            ENDPOINT,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sig,
            },
        )
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000

        data = resp.json()
        status = data.get("status", "?")
        llm_cost = data.get("llm_cost", "$0")

        if status == "queued":
            queued += 1
            icon = "🟢"
        else:
            deduped += 1
            icon = "⚡"

        print(
            f"  {icon} Request {i}/5  "
            f"HTTP {resp.status_code}  "
            f"status={status:<14s}  "
            f"cost={llm_cost}  "
            f"latency={elapsed_ms:.1f}ms"
        )

    print()
    print("─" * 70)
    print(f"  Results:  {queued} queued · {deduped} deduplicated · $0 total LLM cost")
    print("─" * 70)

    if queued != 1 or deduped != 4:
        print("  ❌ FAIL — expected 1 queued + 4 deduplicated")
        sys.exit(1)
    else:
        print("  ✅ PASS — storm suppression working correctly")


if __name__ == "__main__":
    main()
