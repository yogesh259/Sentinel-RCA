#!/usr/bin/env python3
"""Initialise a realistic Java Spring Boot victim repo (./victim-service).

Creates 6 commits with OrderController.java and PaymentService.java:
  #1  Initial OrderController with basic /orders endpoint
  #2  Add PaymentService with Stripe integration
  #3  Wire PaymentService into OrderController
  #4  Add discount-code feature  ← introduces NullPointerException on null discountCode
  #5  Add logging and metrics
  #6  Add database connection pool ← introduces connection leak (never closed)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent / "victim-service"

# ── Helpers ────────────────────────────────────────────────────────


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd or REPO_DIR, check=True, capture_output=True)


def _write(relpath: str, content: str) -> None:
    p = REPO_DIR / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip())


def _commit(msg: str) -> None:
    _run(["git", "add", "-A"])
    _run(["git", "commit", "-m", msg, "--allow-empty"])


# ── Commit payloads ───────────────────────────────────────────────

COMMITS: list[tuple[str, dict[str, str]]] = [
    # ── Commit 1: Initial OrderController ──────────────────────────
    (
        "feat: add initial OrderController with GET /orders",
        {
            "src/main/java/com/victim/controller/OrderController.java": """\
                package com.victim.controller;

                import org.springframework.web.bind.annotation.*;
                import java.util.List;
                import java.util.ArrayList;

                @RestController
                @RequestMapping("/api/orders")
                public class OrderController {

                    private final List<String> orders = new ArrayList<>();

                    @GetMapping
                    public List<String> listOrders() {
                        return orders;
                    }

                    @PostMapping
                    public String createOrder(@RequestBody String orderJson) {
                        orders.add(orderJson);
                        return "{\\"status\\": \\"created\\"}";
                    }
                }
            """,
            "pom.xml": """\
                <?xml version="1.0" encoding="UTF-8"?>
                <project xmlns="http://maven.apache.org/POM/4.0.0"
                         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                         https://maven.apache.org/xsd/maven-4.0.0.xsd">
                    <modelVersion>4.0.0</modelVersion>
                    <parent>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-starter-parent</artifactId>
                        <version>3.2.1</version>
                    </parent>
                    <groupId>com.victim</groupId>
                    <artifactId>order-service</artifactId>
                    <version>1.0.0</version>
                    <dependencies>
                        <dependency>
                            <groupId>org.springframework.boot</groupId>
                            <artifactId>spring-boot-starter-web</artifactId>
                        </dependency>
                        <dependency>
                            <groupId>org.springframework.boot</groupId>
                            <artifactId>spring-boot-starter-data-jpa</artifactId>
                        </dependency>
                    </dependencies>
                </project>
            """,
        },
    ),
    # ── Commit 2: Add PaymentService ───────────────────────────────
    (
        "feat: add PaymentService with Stripe integration",
        {
            "src/main/java/com/victim/service/PaymentService.java": """\
                package com.victim.service;

                import org.springframework.stereotype.Service;
                import java.math.BigDecimal;

                @Service
                public class PaymentService {

                    private static final String STRIPE_API_KEY = "sk_test_placeholder";

                    public String chargeCustomer(String customerId, BigDecimal amount) {
                        // Simulate Stripe API call
                        if (amount.compareTo(BigDecimal.ZERO) <= 0) {
                            throw new IllegalArgumentException("Charge amount must be positive");
                        }
                        String transactionId = "txn_" + System.currentTimeMillis();
                        System.out.println("Charged " + customerId + " $" + amount + " -> " + transactionId);
                        return transactionId;
                    }

                    public boolean refund(String transactionId) {
                        System.out.println("Refunded " + transactionId);
                        return true;
                    }
                }
            """,
        },
    ),
    # ── Commit 3: Wire PaymentService into OrderController ─────────
    (
        "feat: integrate PaymentService into order creation flow",
        {
            "src/main/java/com/victim/controller/OrderController.java": """\
                package com.victim.controller;

                import com.victim.service.PaymentService;
                import org.springframework.beans.factory.annotation.Autowired;
                import org.springframework.web.bind.annotation.*;
                import java.math.BigDecimal;
                import java.util.List;
                import java.util.ArrayList;
                import java.util.Map;

                @RestController
                @RequestMapping("/api/orders")
                public class OrderController {

                    private final List<Map<String, Object>> orders = new ArrayList<>();

                    @Autowired
                    private PaymentService paymentService;

                    @GetMapping
                    public List<Map<String, Object>> listOrders() {
                        return orders;
                    }

                    @PostMapping
                    public Map<String, Object> createOrder(@RequestBody Map<String, Object> body) {
                        String customerId = (String) body.get("customerId");
                        BigDecimal amount = new BigDecimal(body.get("amount").toString());

                        String txnId = paymentService.chargeCustomer(customerId, amount);

                        Map<String, Object> order = Map.of(
                            "customerId", customerId,
                            "amount", amount,
                            "transactionId", txnId,
                            "status", "confirmed"
                        );
                        orders.add(order);
                        return order;
                    }
                }
            """,
        },
    ),
    # ── Commit 4: Add discount-code — INTRODUCES NPE BUG ──────────
    (
        "feat: add discount code support to orders",
        {
            "src/main/java/com/victim/controller/OrderController.java": """\
                package com.victim.controller;

                import com.victim.service.PaymentService;
                import org.springframework.beans.factory.annotation.Autowired;
                import org.springframework.web.bind.annotation.*;
                import java.math.BigDecimal;
                import java.util.List;
                import java.util.ArrayList;
                import java.util.Map;
                import java.util.HashMap;

                @RestController
                @RequestMapping("/api/orders")
                public class OrderController {

                    private final List<Map<String, Object>> orders = new ArrayList<>();

                    @Autowired
                    private PaymentService paymentService;

                    @GetMapping
                    public List<Map<String, Object>> listOrders() {
                        return orders;
                    }

                    @PostMapping
                    public Map<String, Object> createOrder(@RequestBody Map<String, Object> body) {
                        String customerId = (String) body.get("customerId");
                        BigDecimal amount = new BigDecimal(body.get("amount").toString());

                        // BUG: discountCode may be null — .length() throws NullPointerException
                        String discountCode = (String) body.get("discountCode");
                        if (discountCode.length() > 0) {
                            amount = applyDiscount(amount, discountCode);
                        }

                        String txnId = paymentService.chargeCustomer(customerId, amount);

                        Map<String, Object> order = new HashMap<>();
                        order.put("customerId", customerId);
                        order.put("amount", amount);
                        order.put("transactionId", txnId);
                        order.put("discountCode", discountCode);
                        order.put("status", "confirmed");
                        orders.add(order);
                        return order;
                    }

                    private BigDecimal applyDiscount(BigDecimal amount, String code) {
                        if ("SAVE10".equals(code)) {
                            return amount.multiply(new BigDecimal("0.90"));
                        } else if ("SAVE20".equals(code)) {
                            return amount.multiply(new BigDecimal("0.80"));
                        }
                        return amount;
                    }
                }
            """,
        },
    ),
    # ── Commit 5: Add logging and metrics ──────────────────────────
    (
        "feat: add structured logging and order metrics",
        {
            "src/main/java/com/victim/service/PaymentService.java": """\
                package com.victim.service;

                import org.slf4j.Logger;
                import org.slf4j.LoggerFactory;
                import org.springframework.stereotype.Service;
                import java.math.BigDecimal;
                import java.util.concurrent.atomic.AtomicLong;

                @Service
                public class PaymentService {

                    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);
                    private static final String STRIPE_API_KEY = "sk_test_placeholder";
                    private final AtomicLong totalCharges = new AtomicLong(0);

                    public String chargeCustomer(String customerId, BigDecimal amount) {
                        if (amount.compareTo(BigDecimal.ZERO) <= 0) {
                            log.error("Invalid charge amount: {} for customer {}", amount, customerId);
                            throw new IllegalArgumentException("Charge amount must be positive");
                        }
                        String transactionId = "txn_" + System.currentTimeMillis();
                        totalCharges.incrementAndGet();
                        log.info("Charged customer={} amount={} txn={} totalCharges={}",
                                 customerId, amount, transactionId, totalCharges.get());
                        return transactionId;
                    }

                    public boolean refund(String transactionId) {
                        log.info("Refunding transaction={}", transactionId);
                        return true;
                    }

                    public long getTotalCharges() {
                        return totalCharges.get();
                    }
                }
            """,
        },
    ),
    # ── Commit 6: Add DB pool — INTRODUCES CONNECTION LEAK ────────
    (
        "feat: add database connection pool for order persistence",
        {
            "src/main/java/com/victim/service/PaymentService.java": """\
                package com.victim.service;

                import org.slf4j.Logger;
                import org.slf4j.LoggerFactory;
                import org.springframework.stereotype.Service;
                import java.math.BigDecimal;
                import java.sql.Connection;
                import java.sql.DriverManager;
                import java.sql.PreparedStatement;
                import java.sql.SQLException;
                import java.util.concurrent.atomic.AtomicLong;

                @Service
                public class PaymentService {

                    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);
                    private static final String STRIPE_API_KEY = "sk_test_placeholder";
                    private static final String DB_URL = "jdbc:postgresql://localhost:5432/orders";
                    private final AtomicLong totalCharges = new AtomicLong(0);

                    public String chargeCustomer(String customerId, BigDecimal amount) {
                        if (amount.compareTo(BigDecimal.ZERO) <= 0) {
                            log.error("Invalid charge amount: {} for customer {}", amount, customerId);
                            throw new IllegalArgumentException("Charge amount must be positive");
                        }
                        String transactionId = "txn_" + System.currentTimeMillis();
                        totalCharges.incrementAndGet();

                        // BUG: Connection is never closed — resource leak!
                        try {
                            Connection conn = DriverManager.getConnection(DB_URL, "app", "secret");
                            PreparedStatement stmt = conn.prepareStatement(
                                "INSERT INTO payments (customer_id, amount, txn_id) VALUES (?, ?, ?)"
                            );
                            stmt.setString(1, customerId);
                            stmt.setBigDecimal(2, amount);
                            stmt.setString(3, transactionId);
                            stmt.executeUpdate();
                            // conn.close() is MISSING — connection leak
                        } catch (SQLException e) {
                            log.warn("DB write failed (non-fatal): {}", e.getMessage());
                        }

                        log.info("Charged customer={} amount={} txn={} totalCharges={}",
                                 customerId, amount, transactionId, totalCharges.get());
                        return transactionId;
                    }

                    public boolean refund(String transactionId) {
                        log.info("Refunding transaction={}", transactionId);
                        return true;
                    }

                    public long getTotalCharges() {
                        return totalCharges.get();
                    }
                }
            """,
        },
    ),
]


# ── Main ──────────────────────────────────────────────────────────


def main() -> None:
    if REPO_DIR.exists():
        print(f"Removing existing {REPO_DIR} …")
        shutil.rmtree(REPO_DIR)

    REPO_DIR.mkdir(parents=True)
    _run(["git", "init"])
    _run(["git", "config", "user.email", "victim-bot@sentinel-rca.dev"])
    _run(["git", "config", "user.name", "Victim Bot"])

    for idx, (message, files) in enumerate(COMMITS, start=1):
        for relpath, content in files.items():
            _write(relpath, content)
        _commit(message)
        print(f"  ✓ Commit #{idx}: {message}")

    # Show summary
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    print(f"\n{'─' * 60}")
    print(f"Victim repo created at {REPO_DIR}")
    print(f"{'─' * 60}")
    print(result.stdout)

    # Highlight the bugs
    print("🐛 Bugs planted:")
    print("   Commit #4 → NullPointerException on null discountCode  (OrderController.java:34)")
    print("   Commit #6 → Database connection leak — conn.close() missing  (PaymentService.java:33)")


if __name__ == "__main__":
    main()
