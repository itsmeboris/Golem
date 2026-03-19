.PHONY: setup test lint

setup:
	git config core.hooksPath .githooks

test:
	python -m pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

lint:
	python -m black --check golem/
	python -m pylint --errors-only golem/
	python -m pylint --disable=all --enable=W0611,W0612,W0101 golem/
	python scripts/pyflakes_noqa.py golem/
	python -m vulture golem/ vulture_whitelist.py --min-confidence 80
