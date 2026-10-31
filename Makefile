.PHONY: install test eval demo serve

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest tests --cov=claims_processing_cx --cov-report=term-missing \
		--cov-report=json:evals/results/coverage.json

eval:
	python -m evals.generate_dataset
	python -m evals.run_eval

demo:
	python -m claims_processing_cx.conversation

serve:
	python -m uvicorn claims_processing_cx.api:app --host 127.0.0.1 --port 8000
