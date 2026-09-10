# raftlab -- everything here runs with no dependencies beyond Python 3.10+.
PY ?= python3
SEEDS ?= 500

.PHONY: help test test-slow demo campaign recovery reads coverage ablation fuzz lint typecheck clean

help:            ## show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

test:            ## the whole suite, about twelve seconds
	$(PY) -m unittest discover -s tests -q

test-slow:       ## also the rare-bug meta-tests (~90s)
	RAFTLAB_SLOW=1 $(PY) -m unittest discover -s tests -q

demo:            ## a guided tour of what this project does
	./scripts/demo.sh

campaign:        ## correct build plus every planted bug, four worlds
	$(PY) -m raftlab campaign --seeds $(SEEDS)

recovery:        ## how fast the cluster comes back after a heal
	$(PY) -m raftlab recovery --seeds $(SEEDS)

reads:           ## the three read paths of section 6.4, compared
	$(PY) -m raftlab reads --seeds $(SEEDS)

coverage:        ## which situations the fuzzer actually reaches
	$(PY) -m raftlab coverage --seeds 300

ablation:        ## what pre-vote is worth, measured
	$(PY) -m raftlab ablation --seeds $(SEEDS)

fuzz:            ## a long hunt for the rare ones
	$(PY) -m raftlab fuzz --bug no_persist_vote --profile swarm --seeds 5000

lint:            ## ruff, if it is installed
	@command -v ruff >/dev/null && ruff check raftlab tests || echo "ruff not installed: pip install ruff"

typecheck:       ## mypy, if it is installed
	@command -v mypy >/dev/null && mypy raftlab --ignore-missing-imports || echo "mypy not installed: pip install mypy"

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
