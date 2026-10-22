.PHONY: up down build migrate migrate-gen seed test lint format install shell css css-watch

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

migrate:
	alembic upgrade head

migrate-gen:
	alembic revision --autogenerate -m "$(msg)"

seed:
	python -m scripts.seed_data

test:
	pytest -v

lint:
	ruff check app/ tests/

format:
	ruff format app/ tests/
	ruff check --fix app/ tests/

install:
	uv pip install -e ".[dev]"

shell:
	python -c "import asyncio; from app.database import AsyncSessionLocal; print('DB ready')"

superuser:
	python -m scripts.create_superuser

css:
	npx --yes tailwindcss@3.4.17 -i static/css/input.css -o static/css/app.css --minify

css-watch:
	npx --yes tailwindcss@3.4.17 -i static/css/input.css -o static/css/app.css --watch
