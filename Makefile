.PHONY: setup test lint

setup:
	git config core.hooksPath .githooks

test:
	python -m pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

lint:
	python -m black --check golem/
	python -m pylint --errors-only golem/
