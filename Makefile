PYTHONPATH := src
export PYTHONPATH

.PHONY: snapshot pipeline test browser iceberg feldera lint

snapshot:
	python -m gasgap.snapshot

pipeline:
	python -m gasgap.pipeline

lint:
	ruff check src tests
	ruff format --check src tests

test: lint
	pytest -q -m "not browser and not feldera and not iceberg"

browser:
	pytest -q -m browser

iceberg:
	docker compose up -d rustfs nessie
	pytest -q -m iceberg

feldera:
	docker compose up -d feldera
	pytest -q -m feldera
