# Makefile for Clio Agent 1
# Common development tasks

.PHONY: help install test lint format typecheck build clean check all

# Default target
help:
	@echo "Clio Agent 1 - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Install in development mode with all extras"
	@echo "  make install-dev    Install development dependencies only"
	@echo ""
	@echo "Testing:"
	@echo "  make test           Run all tests"
	@echo "  make test-cov       Run tests with coverage"
	@echo "  make test-unit      Run unit tests only"
	@echo "  make test-integration Run integration tests only"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format         Format code with black"
	@echo "  make lint           Run flake8 linter"
	@echo "  make typecheck      Run mypy type checker"
	@echo "  make check          Run all checks (format, lint, typecheck)"
	@echo ""
	@echo "Building:"
	@echo "  make build          Build distribution packages"
	@echo "  make build-check    Build and verify with twine"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build   Build all Docker images"
	@echo "  make docker-test    Test Docker images"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean          Remove build artifacts and cache"
	@echo "  make update-deps    Update dependencies to latest"
	@echo ""

# Installation
install:
	pip install -e ".[dev,all]"

install-dev:
	pip install -e ".[dev]"

# Testing
test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --tb=short --cov=src/ai_agent --cov-report=html --cov-report=term

test-unit:
	pytest tests/ -v --tb=short -m unit

test-integration:
	pytest tests/ -v --tb=short -m integration

test-fast:
	pytest tests/ -v --tb=short -m "not slow"

# Code Quality
format:
	black src tests

lint:
	flake8 src tests

typecheck:
	mypy src

check: format lint typecheck

# Building
build:
	python -m build

build-check: build
	twine check dist/*

# Docker
docker-build:
	for dir in docker/*/; do \
		if [ -f "$$dir/Dockerfile" ]; then \
			echo "Building $$dir..."; \
			docker build -t clio-agent:$$(basename $$dir) "$$dir" || exit 1; \
		fi; \
	done

docker-test:
	docker run --rm clio-agent:ubuntu --check

# Maintenance
clean:
	rm -rf build dist *.egg-info
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

update-deps:
	pip install --upgrade pip
	pip install -e ".[dev,all]" --upgrade

# Release helpers
version-patch:
	bumpversion patch

version-minor:
	bumpversion minor

version-major:
	bumpversion major

# Quick development cycle
dev: install test check
	@echo "Development environment ready!"

# Run agent with health check
health:
	Clio-Agent --health-check

run:
	Clio-Agent

run-supervisor:
	Clio-Agent --supervisor