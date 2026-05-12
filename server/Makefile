.PHONY: build test clean

IMAGE := guise:latest

build:
	docker build -t $(IMAGE) .

test:
	python -m pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
