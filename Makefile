# Quality gates for backend (ruff, mypy, pytest) and frontend (eslint, prettier, vitest).
# `make check` is the single local CI gate (spec §13).

.PHONY: check check-backend check-frontend fmt install

install:
	uv sync
	npm --prefix frontend install

check: check-backend check-frontend

check-backend:
	uv run ruff check backend
	uv run ruff format --check backend
	uv run mypy
	uv run pytest

check-frontend:
	npm --prefix frontend run lint
	npm --prefix frontend run format:check
	npm --prefix frontend run test -- --run

fmt:
	uv run ruff check --fix backend
	uv run ruff format backend
	npm --prefix frontend run format
