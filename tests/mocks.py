"""Deterministic mock fixtures for zero-cost, offline testing."""


import json
import re

import httpx

from src.services.llm_router import LLMResult

MOCK_MODEL = "mock/deterministic"
_CANDIDATE_SHA_RE = re.compile(r'sha="([0-9a-f]{40})"')


def _mock_rca_verdict(prompt: str) -> dict:
    """Pick the first-ranked candidate, so mock evals measure retrieval ranking only."""
    shas = _CANDIDATE_SHA_RE.findall(prompt)
    if not shas:
        return {
            "suspect_sha": None,
            "confidence": "low",
            "severity": "medium",
            "root_cause": "No candidate commit explains this exception.",
        }
    return {
        "suspect_sha": shas[0],
        "confidence": "high",
        "severity": "high",
        "root_cause": "[MOCK_LLM] Exception introduced by the top-ranked candidate commit.",
        "mechanism": "Deterministic mock verdict.",
        "evidence": [{"sha": shas[0], "file": "", "lines": "", "quote": ""}],
        "fix_suggestion": "Guard the failing expression.",
    }


async def mock_llm_complete(
    tier: str, messages: list[dict[str, str]], json_mode: bool = False
) -> LLMResult:
    """Deterministic mock replacement for src.services.llm_router.llm_complete.

    Chooses the reply shape from the schema fields named in the system prompt.
    """
    system = messages[0]["content"] if messages else ""
    prompt = "\n".join(m["content"] for m in messages)
    if "suspect_sha" in system:
        body = _mock_rca_verdict(prompt)
    elif "duplicate_of" in system:
        body = {"duplicate_of": None, "reason": "[MOCK_LLM] Different failure mode."}
    else:
        body = {"exception_type": "UnknownException"}
    return LLMResult(
        content=json.dumps(body),
        model=MOCK_MODEL,
        tier=tier,
        input_tokens=len(prompt) // 4,
        output_tokens=60,
        cost_usd=0.0,
    )


# ── Golden GitHub Mock Data ────────────────────────────────────────

ORDER_CONTROLLER_COMMITS = [
    {
        "sha": "4b66e5724817b75a701ca7e7e5ac46a9d873279a",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-15T12:00:00Z"},
            "message": "feat: add discount code support to orders",
        },
    },
    {
        "sha": "d92579c58ffbc31d331d93c75d4b98f8133c9d2c",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-14T12:00:00Z"},
            "message": "feat: integrate PaymentService into order creation flow",
        },
    },
    {
        "sha": "ebbcdbbffd734f08c501b6bc0e1657d1050e8565",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-13T12:00:00Z"},
            "message": "feat: add initial OrderController with GET /orders",
        },
    },
]

PAYMENT_SERVICE_COMMITS = [
    {
        "sha": "aede1ddfb402e0297929b03f89d8269e2effc411",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-15T12:00:00Z"},
            "message": "feat: add database connection pool for order persistence",
        },
    },
    {
        "sha": "27780cd8c9b9a9e4a6902179567326ce5dffc07d",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-14T12:00:00Z"},
            "message": "feat: add structured logging and order metrics",
        },
    },
    {
        "sha": "2e8081a5566c6b63a4cea73125e4d015d37ca1a9",
        "commit": {
            "author": {"name": "Victim Bot", "date": "2024-01-13T12:00:00Z"},
            "message": "feat: add PaymentService with Stripe integration",
        },
    },
]

ORDER_CONTROLLER_DIFF = """\
diff --git a/src/main/java/com/victim/controller/OrderController.java b/src/main/java/com/victim/controller/OrderController.java
--- a/src/main/java/com/victim/controller/OrderController.java
+++ b/src/main/java/com/victim/controller/OrderController.java
@@ -32,3 +32,5 @@
+        String discountCode = (String) body.get("discountCode");
+        if (discountCode.length() > 0) {
+            amount = applyDiscount(amount, discountCode);
+        }
"""

PAYMENT_SERVICE_DIFF = """\
diff --git a/src/main/java/com/victim/service/PaymentService.java b/src/main/java/com/victim/service/PaymentService.java
--- a/src/main/java/com/victim/service/PaymentService.java
+++ b/src/main/java/com/victim/service/PaymentService.java
@@ -30,6 +30,8 @@
+        Connection conn = DriverManager.getConnection(DB_URL, "app", "secret");
+        PreparedStatement stmt = conn.prepareStatement("INSERT INTO payments VALUES (?, ?, ?)");
+        stmt.executeUpdate();
+        // BUG: conn.close() is missing - resource connection pool leak!
"""

ORDER_CONTROLLER_CONTENT = """\
package com.victim.controller;

import org.springframework.web.bind.annotation.*;
import java.util.Map;

/**
 * OrderController handles order creation and checkout.
 */
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    // Helper methods and fields
    private final Map<String, Object> orderDb = Map.of();

    public OrderController() {
        // Constructor
    }

    public Map<String, Object> getOrder(String id) {
        return orderDb;
    }

    private double applyDiscount(double amount, String code) {
        return amount * 0.9;
    }

    @PostMapping
    public Map<String, Object> createOrder(@RequestBody Map<String, Object> body) {
        double amount = 100.0;
        String customerId = (String) body.get("customerId");
        String discountCode = (String) body.get("discountCode");
        // discount check
        if (discountCode.length() > 0) {
            amount = applyDiscount(amount, discountCode);
        }
        return Map.of("status", "ok", "amount", amount);
    }
}
"""

PAYMENT_SERVICE_CONTENT = """\
package com.victim.service;

import org.springframework.stereotype.Service;
import java.math.BigDecimal;
import java.sql.Connection;

@Service
public class PaymentService {
    public String chargeCustomer(String customerId, BigDecimal amount) {
        if (amount.compareTo(BigDecimal.ZERO) <= 0) {
            throw new IllegalArgumentException("Charge amount must be positive");
        }
        Connection conn = null; // leak
        return "txn_123";
    }
}
"""


REPO_TREE_PATHS = [
    "README.md",
    "pom.xml",
    "src/main/java/com/victim/VictimApplication.java",
    "src/main/java/com/victim/controller/OrderController.java",
    "src/main/java/com/victim/service/PaymentService.java",
    "src/test/java/com/victim/controller/OrderControllerTest.java",
]

# (start_line, end_line, sha, message) — which commit last touched each line block.
BLAME_RANGES = {
    "src/main/java/com/victim/controller/OrderController.java": [
        (1, 27, ORDER_CONTROLLER_COMMITS[2]["sha"], "feat: add initial OrderController with GET /orders"),
        (28, 31, ORDER_CONTROLLER_COMMITS[1]["sha"], "feat: integrate PaymentService into order creation flow"),
        (32, 36, ORDER_CONTROLLER_COMMITS[0]["sha"], "feat: add discount code support to orders"),
        (37, 80, ORDER_CONTROLLER_COMMITS[2]["sha"], "feat: add initial OrderController with GET /orders"),
    ],
    "src/main/java/com/victim/service/PaymentService.java": [
        (1, 26, PAYMENT_SERVICE_COMMITS[2]["sha"], "feat: add PaymentService with Stripe integration"),
        (27, 29, PAYMENT_SERVICE_COMMITS[1]["sha"], "feat: add structured logging and order metrics"),
        (30, 36, PAYMENT_SERVICE_COMMITS[0]["sha"], "feat: add database connection pool for order persistence"),
        (37, 80, PAYMENT_SERVICE_COMMITS[2]["sha"], "feat: add PaymentService with Stripe integration"),
    ],
}


def _graphql_blame_response(request: httpx.Request) -> httpx.Response:
    """Mimic GitHub GraphQL `blame` for the default branch."""
    path = json.loads(request.content)["variables"]["path"]
    ranges = [
        {
            "startingLine": start,
            "endingLine": end,
            "commit": {
                "oid": sha,
                "committedDate": "2024-01-15T12:00:00Z",
                "messageHeadline": message,
                "author": {"name": "Victim Bot"},
            },
        }
        for start, end, sha, message in BLAME_RANGES.get(path, [])
    ]
    if not ranges:
        return httpx.Response(200, json={"data": None, "errors": [{"message": "path not found"}]})
    target = {"blame": {"ranges": ranges}}
    return httpx.Response(
        200, json={"data": {"repository": {"defaultBranchRef": {"target": target}}}}
    )


def github_mock_handler(request: httpx.Request) -> httpx.Response:
    """Mock handler for GitHub REST + GraphQL API calls."""
    url = str(request.url)
    accept = request.headers.get("accept", "")

    if url.endswith("/graphql"):
        return _graphql_blame_response(request)

    if "/git/trees/" in url:
        tree = [{"path": p, "type": "blob"} for p in REPO_TREE_PATHS]
        return httpx.Response(200, json={"tree": tree, "truncated": False})

    # Commits listing
    if "/commits" in url and "path=" in url:
        if "OrderController" in url:
            return httpx.Response(200, json=ORDER_CONTROLLER_COMMITS)
        elif "PaymentService" in url:
            return httpx.Response(200, json=PAYMENT_SERVICE_COMMITS)
        else:
            return httpx.Response(200, json=ORDER_CONTROLLER_COMMITS[:1])

    # Single commit diff
    if "/commits/" in url:
        sha = url.split("/commits/")[-1].split("?")[0]
        if "vnd.github.diff" in accept:
            if "aede1dd" in sha or "27780cd" in sha or "2e8081a" in sha:
                return httpx.Response(200, text=PAYMENT_SERVICE_DIFF)
            return httpx.Response(200, text=ORDER_CONTROLLER_DIFF)
        return httpx.Response(200, json={"sha": sha, "files": []})

    # File contents
    if "/contents/" in url:
        if "OrderController" in url:
            return httpx.Response(200, text=ORDER_CONTROLLER_CONTENT)
        elif "PaymentService" in url:
            return httpx.Response(200, text=PAYMENT_SERVICE_CONTENT)
        return httpx.Response(200, text="// content")

    return httpx.Response(404, json={"message": "Not Found"})


_RealAsyncClient = httpx.AsyncClient


def create_mock_github_client(*args, **kwargs) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient configured with MockTransport."""
    kwargs["transport"] = httpx.MockTransport(github_mock_handler)
    return _RealAsyncClient(*args, **kwargs)
