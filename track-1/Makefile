# Bundles live in data/<SET>/ -- see GET_DATA.md.
SET ?= Market-cloudbed-1
SUB ?= starter
PY  ?= python3
MODE ?= routed
OUT ?= out/dev/$(MODE)
RUNS_ROOT ?= out/dev
DOCKER_OUT ?= out/docker
WITH_ENV = $(PY) $(CURDIR)/scripts/with_env.py
# which agent module run.py loads, e.g. AGENT=agents.routed (default: run.py's)
AGENT_ARG := $(if $(AGENT),--agent $(AGENT),)

.PHONY: help validate dev score cost compare docker
.DEFAULT_GOAL := help

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

validate: ## run the DEFAULT agent on a one- and a two-failure dev case and check the output shape
	RCA_ROUTING_MODE=$(MODE) $(WITH_ENV) $(PY) scripts/validate_submission.py --submission $(SUB) \
	  --dataset data/$(SET) --queries data/$(SET)/dev/query_dev.csv --row-ids 0,27 $(AGENT_ARG)

dev: ## run all 70 cases for MODE=rules|cheap|strong|routed (N=20 limits cases)
	cd $(SUB) && RCA_ROUTING_MODE=$(MODE) $(WITH_ENV) $(PY) run.py --dataset ../data/$(SET) \
	  --queries ../data/$(SET)/dev/query_dev.csv --out ../$(OUT) $(AGENT_ARG) $(if $(N),--limit $(N),)

score: ## score OUT against the development answers
	cd $(SUB) && $(PY) score.py --predictions ../$(OUT)/predictions.csv \
	  --queries ../data/$(SET)/dev/query_dev.csv

cost: ## calculate dollars per case and model for OUT
	cd $(SUB) && $(PY) cost.py ../$(OUT)/usage.jsonl

compare: ## compare rules, cheap, strong, and routed on identical completed cases
	$(PY) scripts/compare_modes.py --runs-root $(RUNS_ROOT) \
	  --queries data/$(SET)/dev/query_dev.csv \
	  --json-out $(RUNS_ROOT)/comparison.json

docker: ## build the ROOT image and run 2 dev cases exactly as judges will (uses FEATHERLESS_API_KEY)
	@test -d "data/$(SET)/telemetry" && test -f "data/$(SET)/dev/query_dev.csv" || \
	  { echo "Dataset unavailable: data/$(SET). Connect its drive or download the dataset first." >&2; exit 1; }
	docker build -t rca-submission ..
	mkdir -p $(DOCKER_OUT)
	$(WITH_ENV) docker run --rm --cpus=2 --memory=8g -e FEATHERLESS_API_KEY -e FEATHERLESS_BASE_URL \
	  -e RCA_ROUTING_MODE=$(MODE) \
	  -v "$(realpath data/$(SET))":/data:ro -v "$(abspath $(DOCKER_OUT))":/out rca-submission \
	  python run.py --dataset /data --queries /data/dev/query_dev.csv --out /out --limit 2 $(AGENT_ARG)
	@ls $(DOCKER_OUT)/evidence | wc -l | xargs echo "evidence files:"
