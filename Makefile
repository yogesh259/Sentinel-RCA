.PHONY: help migrate migration check-schema simulate-outage test test-eval

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

help:
	@echo "Sentinel-RCA Commands:"
	@echo "  make migrate            - Apply pending DB migrations (needs DATABASE_URL)"
	@echo "  make migration m=\"...\" - Draft a migration from model changes (review it!)"
	@echo "  make check-schema       - Fail if src/db/models.py and the database disagree"
	@echo "  make simulate-outage    - Fire 5 rapid webhooks to test storm deduplication"
	@echo "  make test               - Run benchmark evaluation suite"
	@echo "  make test-eval          - Run benchmark evaluation suite"

migrate:
	uv run python -m src.db.migrate

migration:
	uv run alembic revision --autogenerate -m "$(m)"

check-schema:
	uv run alembic check

simulate-outage:
	@SECRET=$${WEBHOOK_SECRET:-local-dev-secret-123}; \
	PAYLOAD='{"action":"created","data":{"event":{"event_id":"sim-outage-001","title":"NullPointerException: discountCode.length()","culprit":"com.victim.controller.OrderController.createOrder","timestamp":1700000000.0,"exception":{"values":[{"type":"NullPointerException","value":"Cannot invoke String.length() because discountCode is null","stacktrace":{"frames":[{"filename":"OrderController.java","lineno":34,"function":"createOrder"}]}}]}}}}'; \
	SIG=$$(printf '%s' "$$PAYLOAD" | openssl dgst -sha256 -hmac "$$SECRET" | awk '{print $$NF}'); \
	echo "Simulating 5 webhook hits to http://localhost:8000/v1/incidents/ingest ..."; \
	for i in 1 2 3 4 5; do \
		echo "Hit $$i/5:"; \
		curl -s -X POST http://localhost:8000/v1/incidents/ingest \
			-H "Content-Type: application/json" \
			-H "X-Webhook-Signature: $$SIG" \
			-d "$$PAYLOAD"; \
		echo ""; \
	done

test:
	$(PYTEST) -m eval

test-eval:
	$(PYTEST) -m eval
