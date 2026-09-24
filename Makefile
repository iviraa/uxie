.PHONY: install lint test build deploy clean

export PYTHONPATH := src:.

install:
	pip install -r requirements-dev.txt

lint:
	ruff check .
	ruff format --check .
	mypy src/critic src/lambdas bench

test:
	pytest -q

build:
	sam build -t infrastructure/template.yaml

deploy: build
	sam deploy -t .aws-sam/build/template.yaml \
		--stack-name critic \
		--capabilities CAPABILITY_IAM \
		--resolve-s3 \
		--parameter-overrides $$(jq -r 'to_entries | map("\(.key)=\(.value)") | join(" ")' infrastructure/parameters.json)

clean:
	rm -rf .aws-sam .mypy_cache .ruff_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
