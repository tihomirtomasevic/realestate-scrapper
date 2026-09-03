# adcrawler — one entry point for the commands you actually run.
#
# `make` on its own lists every target. Everything here wraps docker compose;
# nothing is hidden, so `make -n up` shows you the command it would run.

SHELL := /bin/bash
COMPOSE := docker compose
# The crawler sits behind a compose profile so it never starts by surprise.
CRAWL := $(COMPOSE) --profile crawl
# One-off crawler commands: no scheduler, no dependency start, and the
# container is removed on exit.
RUN := $(COMPOSE) run --rm --no-deps -T crawler python -u -m crawler.run

# Read the published ports back from .env so the URLs printed below are true
# even when someone has changed them.
WEB_PORT := $(shell [ -f .env ] && grep -E '^WEB_PORT=' .env | cut -d= -f2 || echo 8080)
API_PORT := $(shell [ -f .env ] && grep -E '^API_PORT=' .env | cut -d= -f2 || echo 8000)

.DEFAULT_GOAL := help
.PHONY: help up down stop restart logs ps build rebuild \
        crawl crawl-once scheduler scheduler-stop validate verify-gone \
        dedup phash psql shell test check check-env clean nuke

## ── Everyday ──────────────────────────────────────────────────────────────

help:            ## List every target
	@echo "adcrawler — make targets"
	@echo
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  App        http://localhost:$(WEB_PORT)"
	@echo "  API docs   http://localhost:$(API_PORT)/docs"

up: check-env    ## Start the app (db + api + web) and wait until it answers
	$(COMPOSE) up -d
	@echo -n "waiting for the API"
	@for i in $$(seq 1 30); do \
	  if curl -fsS "http://localhost:$(API_PORT)/api/health" >/dev/null 2>&1; then \
	    echo " — ready"; \
	    echo "   app      http://localhost:$(WEB_PORT)"; \
	    echo "   api docs http://localhost:$(API_PORT)/docs"; \
	    exit 0; \
	  fi; \
	  echo -n "."; sleep 2; \
	done; \
	echo; echo "API did not come up. Try: make logs"; exit 1

down:            ## Stop everything and remove the containers (data is kept)
	$(CRAWL) down

stop:            ## Pause the containers without removing them
	$(CRAWL) stop

restart: down up ## Full stop and start

ps:              ## What is running
	@$(CRAWL) ps

logs:            ## Follow the logs (make logs S=crawler for one service)
	$(CRAWL) logs -f --tail=100 $(S)

## ── Building ──────────────────────────────────────────────────────────────

build:           ## Build the images
	$(CRAWL) build

rebuild:         ## Rebuild and restart — use after changing api/, web/ or crawler/
	$(COMPOSE) up -d --build
	@echo "rebuilt. If the scheduler was running: make scheduler"

## ── Collecting ────────────────────────────────────────────────────────────
# The crawler is opt-in. `crawl` is a single pass you watch; `scheduler` is the
# background loop that also serves the app's "Collect Ads Now" button.

crawl: crawl-once  ## Alias for crawl-once

crawl-once:      ## One crawl pass over every configured source, then exit
	$(RUN) --once

scheduler:       ## Start the background crawler (every CRAWL_INTERVAL_MINUTES)
	$(CRAWL) up -d crawler
	@echo "scheduler running — make logs S=crawler to watch it"

scheduler-stop:  ## Stop the background crawler, leave the app up
	$(COMPOSE) stop crawler

validate:        ## Check configs and selectors against one live page
	$(RUN) --validate

gate: ## Classify unfinished / ruin / for-adaptation on ads with no current verdict
	$(RUN) --gate
	@echo "gated properties: $$($(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-adcrawler} -d $${POSTGRES_DB:-adcrawler} -t -A -c 'SELECT count(*) FROM v_property_gate WHERE gated')"

verify-gone:     ## Re-check ads that vanished from search and settle their status
	$(RUN) --verify-gone

dedup:           ## Re-score and re-cluster everything already stored
	$(RUN) --dedup-only

phash:           ## Hash any images that have no perceptual hash yet
	$(RUN) --phash-only

## ── Development ───────────────────────────────────────────────────────────

psql:            ## Open a psql shell on the database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-adcrawler} -d $${POSTGRES_DB:-adcrawler}

shell:           ## Shell inside the crawler container
	$(COMPOSE) run --rm --no-deps -it crawler bash

test:            ## Run the offline test suites
	$(COMPOSE) run --rm --no-deps -T crawler python -m crawler.test_dedup
	$(COMPOSE) run --rm --no-deps -T crawler python -m crawler.test_pagination

check:           ## Pre-commit checks: no target site leaked, no unsafe SQL params
	./scripts/check-no-leaks.sh

## ── Teardown ──────────────────────────────────────────────────────────────

clean:           ## Remove containers AND images, but keep the database
	$(CRAWL) down --rmi local

nuke:            ## Delete EVERYTHING including the database volume
	@echo "This deletes the database volume: every listing and all price history."
	@read -p "Type the word 'delete' to confirm: " ans; \
	  [ "$$ans" = delete ] || { echo "aborted"; exit 1; }
	$(CRAWL) down -v

# Refuse to start against a missing or unedited .env rather than failing later
# with a confusing Postgres authentication error. Phony on purpose: a rule named
# after the file would be skipped whenever the file merely exists, which is
# exactly the case the placeholder check needs to catch.
check-env:
	@if [ ! -f .env ]; then \
	  echo "No .env yet. Run:  cp .env.example .env  then set POSTGRES_PASSWORD"; \
	  exit 1; \
	fi
	@if grep -q '^POSTGRES_PASSWORD=change-me-before-first-run' .env; then \
	  echo "POSTGRES_PASSWORD in .env is still the placeholder — set a real one."; \
	  exit 1; \
	fi
