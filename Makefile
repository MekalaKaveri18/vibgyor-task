PY ?= python

setup:            ## create a Python 3.11 venv with everything installed (needs uv: https://docs.astral.sh/uv/)
	uv venv --python 3.11 .venv
	uv pip install --python .venv/bin/python -r requirements.txt

all:              ## drawing -> model -> renders -> glb
	$(PY) build.py all

quick:            ## same, with low-res preview renders (minutes instead of an hour)
	$(PY) build.py all --quick

serve:            ## interactive viewer on http://localhost:8000
	$(PY) build.py serve

test:
	$(PY) build.py test

.PHONY: setup all quick serve test
