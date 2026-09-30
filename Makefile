.PHONY: install lint test build deploy dispatcher dashboard clean

export PYTHONPATH := src:.

install:
	pip install -r requirements-dev.txt

lint:
	ruff check .
	ruff format --check .
	mypy src/uxie src/lambdas bench

test:
	pytest -q

build:
	sam build -t infrastructure/template.yaml

deploy: build
	sam deploy -t .aws-sam/build/template.yaml \
		--stack-name uxie \
		--capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
		--resolve-s3 \
		--parameter-overrides $$(jq -r 'to_entries | map("\(.key)=\(.value)") | join(" ")' infrastructure/parameters.json)

clean:
	rm -rf .aws-sam .mypy_cache .ruff_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

dispatcher:  ## rebuild + roll out the verify dispatcher on the local k3s cluster
	./deploy/dispatcher.sh

dashboard:  ## rebuild the Grafana dashboard from code and upload it
	python deploy/grafana/build_dashboard.py
	python deploy/grafana/push.py $${GRAFANA_URL:?set GRAFANA_URL=https://<stack>.grafana.net}
