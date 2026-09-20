.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

.PHONY: setup test run report pending api mcp mcp-http clean

setup: .venv/bin/python
	@test -f .env || cp .env.example .env
	@echo "Done. Edit .env (TYPESAFE_API_KEY), then: make test"

test: .venv/bin/python
	PYTHONPATH=. .venv/bin/python tests/test_smoke.py

# usage: make run REQUEST="where is my second invoice copy?"
run: .venv/bin/python
	PYTHONPATH=. .venv/bin/python -c "from src.router import route; print(route('$(REQUEST)'))"

report: .venv/bin/python
	PYTHONPATH=. .venv/bin/python -m eval.report --log logs/decisions.jsonl

pending: .venv/bin/python
	PYTHONPATH=. .venv/bin/python -m eval.label --pending

api: .venv/bin/python
	.venv/bin/uvicorn server:app --port 8000

mcp: .venv/bin/python
	PYTHONPATH=. .venv/bin/python mcp_server.py

mcp-http: .venv/bin/python
	PYTHONPATH=. .venv/bin/python mcp_server.py --http --port 8001

clean:
	rm -rf .venv src/__pycache__ eval/__pycache__ tests/__pycache__
