# -----------------------------------------------------------------------------
#  Gravitee Lab
# -----------------------------------------------------------------------------

# A target that stands for a `docker compose` command shows it, so you can type it yourself; checks and helpers stay
# quiet.

makefile := $(MAKEFILE_LIST)
SVC ?=

# The Python that makes .venv, and that runs the lab when there is no .venv: PYTHON3=python3.12 make setup
PYTHON3 ?= python3

# The Python to run: the one of .venv when `make setup` made it, else PYTHON3
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON3))

# The core file's name: stop, down and reset name the project instead of reading the files, so they reach every
# container and volume of the lab, also those of an add-on turned off since, as the Lab UI does
project := $(shell sed -n 's/^name: *//p' docker-compose.yml)

# What is on is .lab/on.yml, which Compose reads with the core: through this for the targets below, through .env for a
# `docker compose` typed by hand.
on := .lab/on.yml
export COMPOSE_FILE := docker-compose.yml:$(on)

help:   # what `make` alone prints
	@color=0; [ -t 1 ] && [ -z "$$NO_COLOR" ] && color=1; \
	awk -v color=$$color 'BEGIN {if (color) {bold = "\033[1m"; cyan = "\033[36m"; off = "\033[0m"}} \
		/^##@/ {printf "\n%s%s%s\n", bold, substr($$0, 5), off} \
		/^[a-zA-Z0-9_-]+:.*## / {name = $$0; sub(/:.*/, "", name); text = $$0; sub(/^[^#]*## /, "", text); \
			printf "  %s%-10s%s %s\n", cyan, name, off, text}' $(makefile)

# What the targets need, checked first so that a missing tool is one line and not a stack trace
readme := (see Prerequisites in README.md)

docker-cli:
	@command -v docker >/dev/null 2>&1 || { \
		echo "docker not found: install Docker with Compose 5.0 or later $(readme)"; exit 1; }

compose-ok: docker-cli
	@v=$$(docker compose version --short 2>/dev/null | sed 's/^v//'); case "$${v%%.*}" in ''|*[!0-9]*|[0-4]) \
		echo "Docker Compose 5.0 or later is needed, found: $${v:-none} $(readme)"; exit 1;; esac

# On Linux, a user outside the docker group gets a permission error from a Docker that runs: say that, not "not running"
docker-running: docker-cli
	@err=$$(docker info 2>&1 >/dev/null) && exit 0; \
		case "$$err" in \
		*[Pp]ermission?denied*) \
			printf '%s\n' "$$err" | tail -n 1; \
			echo 'Docker is running but your user cannot reach it: add yourself to the docker group' \
				'(sudo usermod -aG docker $$USER), then log in again (see Troubleshooting in docs/troubleshooting.md)';; \
		*) echo "Docker is not running: start Docker Desktop (or the docker service), then try again";; \
		esac; exit 1

docker-up: compose-ok docker-running

python-ok:
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
		echo "$(PYTHON) not found: install Python 3.9 or later $(readme)"; exit 1; }
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { \
		echo "Python 3.9 or later is needed, found: $$($(PYTHON) -V 2>&1) $(readme)"; exit 1; }

openssl-ok:
	@command -v openssl >/dev/null 2>&1 || { echo "openssl not found: install it $(readme)"; exit 1; }

##@ Run the lab

# Docker is not checked here: the Lab UI's first step checks the machine and gives the fix of what is missing, Docker included
ui: python-ok init ## Open the Lab UI on http://localhost:9000
	@$(PYTHON) lab-ui/server.py

# A machine that was updated from the layout before catalogs still names add-ons by their old paths in .lab/on.yml, which Compose
# cannot open any more: those targets stop and say what to do. The ones that use the project name and no files (stop, down,
# reset) and the ones that move the settings (ui, init, catalogs, update) never stop here.
legacy-ok: $(on)
	@if grep -qE '^[[:space:]]*-[[:space:]]*"?(addons|setups|playgrounds)/' $(on) 2>/dev/null; then \
		echo "This Lab was updated: run make update to move your settings."; exit 1; fi

all: docker-up legacy-ok init ## Create and start the core and what is on
	docker compose up -d --remove-orphans

# The plugin folders are made here, writable by all: Docker would make them as root, and the containers unpack plugins
# there as uid 1001 (.work). Nothing is made for an add-on: what one needs, it makes itself when it starts.
init: openssl-ok $(on) ## Create what a fresh clone lacks: .env, demo certificates, plugin folders, .lab/on.yml (ui and all run it)
	@[ -f .env ] || { cp .env.example .env && chmod 600 .env && echo "Created .env from .env.example"; }
	@grep -q '^COMPOSE_FILE=' .env || printf '\n%s\nCOMPOSE_FILE=%s\n' \
		'# The files Compose reads: the core, and what is on. Keep it: without it, docker compose runs the core alone' \
		"$$COMPOSE_FILE" >> .env
	@[ -f certs/mtls/server.p12 ] || sh certs/mtls/generate.sh
	@mkdir -p -m 777 plugins/apim plugins/am

$(on):
	@mkdir -p .lab
	@touch $@

start: docker-up legacy-ok $(on) ## Start stopped containers
	docker compose start

stop: docker-up $(on) ## Stop all containers (keeps data)
	docker compose -p $(project) stop

restart: docker-up legacy-ok $(on) ## Restart containers (SVC=name for one service)
	docker compose restart $(SVC)

down: docker-up $(on) ## Remove all containers and networks (keeps data volumes)
	docker compose -p $(project) down

catalogs: python-ok ## Download the catalogs that have no copy yet, then move what an update left behind
	@$(PYTHON) lab-ui/catalog.py provision

update: python-ok ## Pull the Lab (fast-forward only), then run make catalogs
	git pull --ff-only
	@$(MAKE) --no-print-directory catalogs

reset: docker-up $(on) ## DANGER: remove containers AND data (volumes, what seeds created); asks first
	@printf "This deletes all the data: the core's and every add-on's. Type 'yes': "; read answer; [ "$$answer" = "yes" ]
	docker compose -p $(project) down -v
	@for record in playgrounds/*/.state/created.json .lab/catalogs/*/playgrounds/*/.state/created.json; do \
		[ -f "$$record" ] || continue; \
		echo '[]' > "$$record" 2>/dev/null || echo "Could not empty $$record: delete it (sudo may be needed)"; \
	done

ps: docker-up legacy-ok $(on) ## Show container status and health
	docker compose ps -a --orphans

logs: docker-up legacy-ok $(on) ## Follow logs (SVC=name for one service)
	docker compose logs -f --tail=100 $(SVC)

config: compose-ok legacy-ok $(on) ## Validate the core and what is on; SVC=name prints that service's final config
	@docker compose config -q && echo "compose OK"
	@if [ -n "$(SVC)" ]; then docker compose config $(SVC); fi

pull: docker-up legacy-ok $(on) ## Pull the images in use (refreshes moving tags like latest)
	docker compose pull

mongo: docker-up legacy-ok $(on) ## Open a mongosh shell
	docker compose exec mongodb mongosh

certs: openssl-ok ## Regenerate the TLS / mTLS demo certificates (certs/mtls/)
	@sh certs/mtls/generate.sh

urls: python-ok ## Print what is on and the URL of every console
	@$(PYTHON) lab-ui/server.py --list

##@ Work on the lab

# The seeder's dependencies (requests, redis) need Python 3.10; the Lab UI itself runs on 3.9. A .venv made by this run
# with a Python that is too old is removed, so that the next run starts clean; an existing one is left to its owner.
setup: ## Make .venv for the seeder's tests and make lint (needs Python 3.10 or later)
	@made=; [ -d .venv ] || { $(PYTHON3) -m venv .venv && made=1; } || { rm -rf .venv; \
		echo "Cannot make .venv: install Python 3.10 or later with venv (Debian, Ubuntu: apt install python3-venv)"; exit 1; }; \
	.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { \
		found="$$(.venv/bin/python -V 2>&1)"; \
		if [ -n "$$made" ]; then rm -rf .venv; echo "The seeder needs Python 3.10 or later, found: $$found"; again=Run; \
		else echo "The seeder needs Python 3.10 or later, .venv has $$found"; again="Remove .venv, then run"; fi; \
		echo "$$again make setup again with a newer python3 (for example PYTHON3=python3.12 make setup)"; exit 1; }; \
	.venv/bin/python -m pip install --quiet --disable-pip-version-check -r seeder/requirements.txt 'ruff~=0.17.0' && \
	echo "Ready: make test runs the seeder's tests too, and make lint works"

check: python-ok ## Check the core (a catalog: python3 lab-ui/catalog.py check PATH)
	@$(PYTHON) lab-ui/server.py --check

lint: python-ok ## Check the Python code with ruff (needs make setup)
	@$(PYTHON) -m ruff --version >/dev/null 2>&1 || { \
		echo "ruff not found: run make setup (Python 3.10 or later)"; exit 1; }
	@$(PYTHON) -m ruff check .

test: python-ok ## Run the unit tests of the Lab UI (in parallel), the seeder and the tooling (no Docker needed)
	@$(PYTHON) lab-ui/tests/run.py
	@$(PYTHON) -m unittest discover -s .github/tests -t .github/tests
	@if $(PYTHON) -c 'import requests, yaml, redis' 2>/dev/null; then \
		$(PYTHON) -m unittest discover -s seeder/tests -t seeder; \
	else echo "Seeder tests skipped: run make setup (Python 3.10 or later)"; fi

# The Node image a catalog's apps run their JavaScript tests in (catalog.py ci reads this line, lab_ui/catalogs/ci/apptests.py): Node 24 (LTS)
# pinned by the digest of its multi-platform index, so every machine tests with the same image.
NODE_IMAGE ?= node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53

# The run starts the item from nothing and removes what it made: it refuses a machine that holds a lab, which is what a person
# left, so it belongs to CI. ID is kind:id, TESTED the number of a set of x-gravitee.tested, from 0.
e2e: docker-up init ## Start an add-on, setup or playground on a machine with no lab and check it works (ID=KIND:NAME, TESTED=0)
	@[ -n "$(ID)" ] || { echo "Name the item: make e2e ID=KIND:NAME (TESTED=0 runs on the first set of versions it was tested with)"; exit 2; }
	@$(PYTHON) lab-ui/server.py --e2e "$(ID)" $(if $(TESTED),--tested $(TESTED))

smoke: python-ok ## Check the Lab UI against the running stack (read-only)
	@$(PYTHON) lab-ui/tests/smoke.py

.DEFAULT_GOAL := help
.PHONY: help docker-cli compose-ok docker-running docker-up python-ok openssl-ok init ui all legacy-ok catalogs update start stop restart down \
	reset ps logs config pull mongo certs urls setup check lint test e2e smoke
