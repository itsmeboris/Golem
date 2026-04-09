.PHONY: setup test lint mutation mutation-report plugin-data

setup:
	git config core.hooksPath .githooks

test:
	python -m pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

lint:
	python -m black --check golem/
	python -m pylint --errors-only golem/
	python -m pylint --disable=all --enable=W0611,W0612,W0101,W0613 golem/
	python scripts/pyflakes_noqa.py golem/
	python -m vulture golem/ vulture_whitelist.py --min-confidence 80

mutation:  ## Run mutation testing (slow — runs pytest per mutant)
	python -m mutmut run --paths-to-mutate golem/ --tests-dir golem/tests/
	python -m mutmut results

mutation-report:  ## Show surviving mutants from last run
	python -m mutmut results

plugin-data:  ## Manually stage plugin source into golem/_plugin_data for packaging/debugging
	rm -rf golem/_plugin_data
	cp -r plugins/golem golem/_plugin_data
