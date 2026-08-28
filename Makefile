.PHONY: up down simulator worker stream api dashboard demo test test-integration lint format

# ---- Infrastructure ----

up:
	docker compose up -d

down:
	docker compose down

# ---- Pipeline processes ----

simulator:
	python -m src.ingestion.vitals_simulator --patients 20 --rate 1.0

# Lightweight pure-Python processor (no Spark needed)
worker:
	python -m src.processing.stream_worker

# Spark Structured Streaming processor (horizontal-scale path)
stream:
	python -m src.processing.stream_processor

api:
	uvicorn src.api.main:app --reload --port 8000

dashboard:
	streamlit run src/dashboard/app.py --server.port 8501

# Dashboard in demo mode: no Docker, no database, just streamlit
demo:
	VITALS_DEMO_MODE=1 streamlit run src/dashboard/app.py --server.port 8501

# ---- Quality ----

test:
	pytest tests/unit -v --tb=short

test-integration:
	pytest tests/integration -v --tb=short

lint:
	ruff check src/ tests/

format:
	ruff check --fix src/ tests/
	ruff format src/ tests/
