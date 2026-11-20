"""Phase 2 tests — PII redaction, trace_mcp, git_mcp, tracker_mcp.

All tests are deterministic.
- Redaction and trace/git tests are pure (no I/O or mocking needed).
- git_mcp tests run against the real ./victim-service repo.
- tracker_mcp tests use a mocked asyncpg pool.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════
#  1. PII / Secret Redaction
# ═══════════════════════════════════════════════════════════════════


class TestRedaction:
    """Verify that redact_payload masks all sensitive patterns."""

    def _redact(self, text: str):
        from app.core.redaction import redact_payload
        return redact_payload(text)

    # ── JWTs ──────────────────────────────────────────────────────

    def test_jwt_redacted(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        text = f"Authorization: Bearer {jwt}"
        sanitised, types = self._redact(text)
        assert "eyJ" not in sanitised
        assert "[REDACTED_JWT]" in sanitised
        assert "[REDACTED_JWT]" in types

    def test_jwt_in_stack_trace(self):
        trace = (
            "ERROR: Token validation failed for "
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJ1c2VySWQiOiJ1LTk5OSIsInJvbGUiOiJhZG1pbiJ9."
            "abc123signature_value_here_long_enough"
        )
        sanitised, types = self._redact(trace)
        assert "eyJ" not in sanitised
        assert "[REDACTED_JWT]" in types

    # ── Emails ────────────────────────────────────────────────────

    def test_email_redacted(self):
        text = "Contact admin@example.com or support+billing@corp.io for help"
        sanitised, types = self._redact(text)
        assert "admin@example.com" not in sanitised
        assert "support+billing@corp.io" not in sanitised
        assert "[REDACTED_EMAIL]" in sanitised
        assert "[REDACTED_EMAIL]" in types

    def test_email_in_log_line(self):
        text = "User john.doe@company.org logged in at 14:00"
        sanitised, _ = self._redact(text)
        assert "@company.org" not in sanitised

    # ── API keys / secrets ────────────────────────────────────────

    def test_openai_key_redacted(self):
        text = "Using key sk-abc123XYZ456def789ghi012jkl345mno678"
        sanitised, types = self._redact(text)
        assert "sk-abc123" not in sanitised
        assert "[REDACTED_API_KEY]" in types

    def test_github_pat_redacted(self):
        text = "GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"
        sanitised, types = self._redact(text)
        assert "ghp_" not in sanitised
        assert "[REDACTED_API_KEY]" in types

    def test_aws_access_key_redacted(self):
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        sanitised, types = self._redact(text)
        assert "AKIA" not in sanitised
        assert "[REDACTED_API_KEY]" in types

    def test_stripe_key_redacted(self):
        text = "stripe.api_key = sk-fake-abcdefghijklmnopqrstuv"
        sanitised, types = self._redact(text)
        assert "sk_live_" not in sanitised
        assert "[REDACTED_API_KEY]" in types

    # ── Database URIs ─────────────────────────────────────────────

    def test_postgres_uri_redacted(self):
        text = "DSN=postgresql://admin:s3cret@db.prod.internal:5432/orders"
        sanitised, types = self._redact(text)
        assert "postgresql://" not in sanitised
        assert "s3cret" not in sanitised
        assert "[REDACTED_DB_URI]" in types

    def test_jdbc_uri_redacted(self):
        text = 'url: "jdbc:postgresql://localhost:5432/orders"'
        sanitised, types = self._redact(text)
        assert "jdbc:" not in sanitised
        assert "[REDACTED_DB_URI]" in types

    def test_mysql_uri_redacted(self):
        text = "mysql://root:password@mysql-host:3306/app_db"
        sanitised, types = self._redact(text)
        assert "mysql://" not in sanitised
        assert "[REDACTED_DB_URI]" in types

    def test_mongodb_uri_redacted(self):
        text = "mongodb+srv://user:pass@cluster0.abc.mongodb.net/mydb"
        sanitised, types = self._redact(text)
        assert "mongodb+srv://" not in sanitised
        assert "[REDACTED_DB_URI]" in types

    # ── Combined / edge cases ─────────────────────────────────────

    def test_multiple_types_in_one_string(self):
        text = (
            "Error at admin@corp.com connecting to "
            "jdbc:postgresql://prod:5432/db with key sk-longapikey1234567890abcdef"
        )
        sanitised, types = self._redact(text)
        assert "@corp.com" not in sanitised
        assert "jdbc:" not in sanitised
        assert "sk-long" not in sanitised
        assert len(types) == 3  # DB_URI, API_KEY, EMAIL

    def test_no_false_positives_on_clean_text(self):
        text = "The order shipped on 2024-01-15 for $29.99 to 123 Main St."
        sanitised, types = self._redact(text)
        assert sanitised == text
        assert types == []

    def test_redaction_types_are_deduplicated(self):
        text = "user1@a.com and user2@b.com and user3@c.com"
        _, types = self._redact(text)
        assert types.count("[REDACTED_EMAIL]") == 1


# ═══════════════════════════════════════════════════════════════════
#  2. trace_mcp — Stack-trace parsing
# ═══════════════════════════════════════════════════════════════════


class TestTraceMCP:
    """Verify trace_mcp parses Java stack traces and redacts secrets."""

    def test_parse_java_npe_trace(self):
        from app.mcp_servers.trace_mcp import parse_stack_trace

        raw = (
            "java.lang.NullPointerException: Cannot invoke String.length()\n"
            "\tat org.springframework.boot.SpringApplication.run(SpringApplication.java:1352)\n"
            "\tat org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:1067)\n"
            "\tat com.victim.controller.OrderController.createOrder(OrderController.java:34)\n"
        )
        result = parse_stack_trace(raw)

        assert result.exception_type == "NullPointerException"
        assert "Cannot invoke String.length()" in result.exception_message
        assert result.target_file == "OrderController.java"
        assert result.class_name == "com.victim.controller.OrderController"
        assert result.method_name == "createOrder"
        assert result.line_number == 34

    def test_parse_trace_with_embedded_secrets(self):
        from app.mcp_servers.trace_mcp import parse_stack_trace

        raw = (
            "java.sql.SQLException: Failed to connect to "
            "jdbc:postgresql://prod-db:5432/orders user=admin@corp.com\n"
            "\tat com.victim.service.PaymentService.chargeCustomer(PaymentService.java:33)\n"
        )
        result = parse_stack_trace(raw)

        # Secrets must be redacted
        assert "jdbc:" not in result.exception_message
        assert "@corp.com" not in result.exception_message
        assert "[REDACTED_DB_URI]" in result.redaction_types
        assert "[REDACTED_EMAIL]" in result.redaction_types

        # Parsing still works
        assert result.exception_type == "SQLException"
        assert result.target_file == "PaymentService.java"
        assert result.line_number == 33

    def test_parse_python_traceback(self):
        from app.mcp_servers.trace_mcp import parse_stack_trace

        raw = (
            'Traceback (most recent call last):\n'
            '  File "app/main.py", line 42, in handle_request\n'
            '    result = process(data)\n'
            '  File "app/services/processor.py", line 15, in process\n'
            '    raise ValueError("bad input")\n'
            'ValueError: bad input\n'
        )
        result = parse_stack_trace(raw)

        assert result.exception_type == "ValueError"
        assert result.exception_message == "bad input"
        assert result.target_file == "app/services/processor.py"
        assert result.method_name == "process"
        assert result.line_number == 15

    def test_parse_empty_trace(self):
        from app.mcp_servers.trace_mcp import parse_stack_trace

        result = parse_stack_trace("")
        assert result.exception_type == "UnknownException"


# ═══════════════════════════════════════════════════════════════════
#  3. git_mcp — Read-only git operations on victim-service
# ═══════════════════════════════════════════════════════════════════


class TestGitMCP:
    """Test git_mcp against the real ./victim-service repo."""

    def test_get_recent_commits_for_order_controller(self):
        from app.mcp_servers.git_mcp import get_recent_commits

        commits = get_recent_commits(
            "src/main/java/com/victim/controller/OrderController.java"
        )
        assert len(commits) >= 1
        # At least one commit message should mention "discount" (commit #4)
        messages = [c.message for c in commits]
        assert any("discount" in m.lower() for m in messages)

    def test_commit_4_is_npe_bug(self):
        """Commit #4 (discount code) should be retrievable and contain the NPE line."""
        from app.mcp_servers.git_mcp import get_recent_commits, get_commit_diff

        commits = get_recent_commits(
            "src/main/java/com/victim/controller/OrderController.java",
            limit=10,
        )
        discount_commit = next(
            (c for c in commits if "discount" in c.message.lower()), None
        )
        assert discount_commit is not None

        diff = get_commit_diff(discount_commit.sha)
        assert "discountCode" in diff.diff
        # The NPE bug: calling .length() on possibly-null discountCode
        assert "discountCode.length()" in diff.diff

    def test_get_git_blame(self):
        from app.mcp_servers.git_mcp import get_git_blame

        blame = get_git_blame(
            "src/main/java/com/victim/controller/OrderController.java",
            line_number=34,
        )
        assert blame.commit_sha  # non-empty
        assert blame.line_number == 34
        assert blame.line_content  # non-empty

    def test_get_commit_diff(self):
        from app.mcp_servers.git_mcp import get_recent_commits, get_commit_diff

        commits = get_recent_commits(
            "src/main/java/com/victim/controller/OrderController.java",
            limit=1,
        )
        assert len(commits) >= 1
        diff = get_commit_diff(commits[0].sha)
        assert diff.sha == commits[0].sha
        assert len(diff.diff) > 0

    # ── Shell injection prevention ────────────────────────────────

    def test_rejects_semicolon_in_path(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="disallowed characters"):
            get_recent_commits("foo.java; rm -rf /")

    def test_rejects_backtick_in_path(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="disallowed characters"):
            get_recent_commits("`whoami`.java")

    def test_rejects_pipe_in_path(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="disallowed characters"):
            get_recent_commits("foo.java | cat /etc/passwd")

    def test_rejects_dollar_in_path(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="disallowed characters"):
            get_recent_commits("$(whoami).java")

    def test_rejects_path_traversal(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="path-traversal"):
            get_recent_commits("../../etc/passwd")

    def test_rejects_non_hex_sha(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_commit_diff

        with pytest.raises(GitInputValidationError, match="disallowed characters"):
            get_commit_diff("abc123; rm -rf /")

    def test_rejects_empty_path(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_recent_commits

        with pytest.raises(GitInputValidationError, match="must not be empty"):
            get_recent_commits("")

    def test_rejects_empty_sha(self):
        from app.mcp_servers.git_mcp import GitInputValidationError, get_commit_diff

        with pytest.raises(GitInputValidationError, match="must not be empty"):
            get_commit_diff("")


# ═══════════════════════════════════════════════════════════════════
#  4. tracker_mcp — Issue tracking (mocked Postgres)
# ═══════════════════════════════════════════════════════════════════


class TestTrackerMCP:
    """Test tracker_mcp with mocked asyncpg pool."""

    @pytest.fixture(autouse=True)
    def _mock_pool(self):
        """Patch the _get_pool in tracker_mcp with a fake async pool."""
        self.mock_conn = AsyncMock()
        self.mock_pool = MagicMock()

        # Make pool.acquire() return an async context manager yielding mock_conn
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=self.mock_conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        self.mock_pool.acquire.return_value = cm

        async def fake_get_pool():
            return self.mock_pool

        with patch("app.mcp_servers.tracker_mcp._get_pool", new=fake_get_pool):
            yield

    def test_create_issue(self):
        from app.mcp_servers.tracker_mcp import create_issue

        # Simulate fetchrow returning a record-like dict
        fake_row = {
            "id": 1,
            "title": "NPE in OrderController",
            "exception_type": "NullPointerException",
            "file_name": "OrderController.java",
            "root_cause": "discountCode is null",
            "commit_sha": "4c91256",
            "severity": "high",
            "status": "open",
            "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        }
        self.mock_conn.fetchrow = AsyncMock(return_value=fake_row)

        result = asyncio.get_event_loop().run_until_complete(
            create_issue(
                title="NPE in OrderController",
                root_cause="discountCode is null",
                commit_sha="4c91256",
                severity="high",
                exception_type="NullPointerException",
                file_name="OrderController.java",
            )
        )

        assert result.id == 1
        assert result.title == "NPE in OrderController"
        assert result.severity == "high"
        assert result.status == "open"
        self.mock_conn.fetchrow.assert_called_once()

    def test_create_issue_rejects_invalid_severity(self):
        from app.mcp_servers.tracker_mcp import create_issue

        with pytest.raises(ValueError, match="severity"):
            asyncio.get_event_loop().run_until_complete(
                create_issue(
                    title="Test",
                    root_cause="test",
                    commit_sha="abc",
                    severity="super-critical",  # invalid
                )
            )

    def test_search_existing_issues(self):
        from app.mcp_servers.tracker_mcp import search_existing_issues

        fake_rows = [
            {
                "id": 1,
                "title": "NPE in OrderController",
                "exception_type": "NullPointerException",
                "file_name": "OrderController.java",
                "root_cause": "null discountCode",
                "commit_sha": "4c91256",
                "severity": "high",
                "status": "open",
                "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
            },
        ]
        self.mock_conn.fetch = AsyncMock(return_value=fake_rows)

        results = asyncio.get_event_loop().run_until_complete(
            search_existing_issues("NullPointerException", "OrderController.java")
        )

        assert len(results) == 1
        assert results[0].exception_type == "NullPointerException"
        assert results[0].file_name == "OrderController.java"

    def test_search_returns_empty_list(self):
        from app.mcp_servers.tracker_mcp import search_existing_issues

        self.mock_conn.fetch = AsyncMock(return_value=[])

        results = asyncio.get_event_loop().run_until_complete(
            search_existing_issues("SomeOtherException", "Missing.java")
        )

        assert results == []

    def test_draft_hotfix_pr(self):
        from app.mcp_servers.tracker_mcp import draft_hotfix_pr

        result = asyncio.get_event_loop().run_until_complete(
            draft_hotfix_pr(
                commit_sha="4c91256abcdef",
                file_path="src/main/java/com/victim/controller/OrderController.java",
                patch_summary="Add null check for discountCode before calling .length()",
            )
        )

        assert result.status == "draft"
        assert result.branch_name == "hotfix/4c91256"
        assert result.commit_sha == "4c91256abcdef"
        assert "OrderController.java" in result.title
        assert "null check" in result.patch_summary
        assert "Requires human approval" in result.description

    def test_draft_hotfix_pr_short_sha(self):
        from app.mcp_servers.tracker_mcp import draft_hotfix_pr

        result = asyncio.get_event_loop().run_until_complete(
            draft_hotfix_pr(
                commit_sha="abc",
                file_path="Foo.java",
                patch_summary="fix it",
            )
        )
        assert result.branch_name == "hotfix/abc"
