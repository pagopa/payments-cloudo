# Configurable variables
DOCKER ?= docker
VERSION ?= latest
REGISTRY ?=                          # e.g., ghcr.io/your-org or myregistry.azurecr.io
IMAGE_PREFIX ?= cloudo                  # prefix for image names, e.g., payments => payments-orchestrator, payments-worker
PLATFORM ?= linux/amd64

# App paths
ORCH_PATH := src/core/orchestrator
WORKER_PATH := src/core/worker
FE_PATH := src/core/cloudo-ui
AGENT_PATH := src/core/agent

# Dockerfile paths
ORCH_DOCKERFILE := $(ORCH_PATH)/Dockerfile
WORKER_DOCKERFILE := $(WORKER_PATH)/Dockerfile
FE_DOCKERFILE := $(FE_PATH)/Dockerfile
AGENT_DOCKERFILE := $(AGENT_PATH)/Dockerfile

# Image tags (can be overridden if needed)
ORCH_IMAGE ?= $(strip $(REGISTRY))$(if $(strip $(REGISTRY)),/,)$(strip $(IMAGE_PREFIX))-orchestrator:$(VERSION)
WORKER_IMAGE ?= $(strip $(REGISTRY))$(if $(strip $(REGISTRY)),/,)$(strip $(IMAGE_PREFIX))-worker:$(VERSION)
FE_IMAGE ?= $(strip $(REGISTRY))$(if $(strip $(REGISTRY)),/,)$(strip $(IMAGE_PREFIX))-ui:$(VERSION)
AGENT_IMAGE ?= $(strip $(REGISTRY))$(if $(strip $(REGISTRY)),/,)$(strip $(IMAGE_PREFIX))-agent:$(VERSION)

# Common build args
COMMON_BUILD_ARGS := --build-arg APP_PATH

.PHONY: help
help:
	@echo "Available targets:"
	@echo "  make build                         - Build both images"
	@echo "  make build-orchestrator            - Build the orchestrator image"
	@echo "  make build-worker                  - Build the worker image"
	@echo "  make build-agent                   - Build the agent image"
	@echo "  make push                          - Push both images"
	@echo "  make push-orchestrator             - Push the orchestrator image"
	@echo "  make push-worker                   - Push the worker image"
	@echo "  make push-agent                    - Push the agent image"
	@echo "  make clean                         - Remove local images (matching tags only)"
	@echo "  make test-env-start                - Start local dev test environment"
	@echo "  make test-env-stop                 - Stop local dev test environment"
	@echo "  make test-env-restart              - Restart local dev test environment"
	@echo "  make test                          - Run unit tests for orchestrator, worker and agent"
	@echo "  make test-orchestrator             - Run orchestrator unit tests only"
	@echo "  make test-worker                   - Run worker unit tests only"
	@echo "  make test-agent                    - Run agent unit tests only"
	@echo ""
	@echo "Overridable variables:"
	@echo "  VERSION=<tag>                      (default: latest)"
	@echo "  REGISTRY=<registry>                (e.g., ghcr.io/your-org, myregistry.azurecr.io)"
	@echo "  IMAGE_PREFIX=<prefix>              (default: app; e.g., payments)"
	@echo "  ORCH_IMAGE / WORKER_IMAGE          (full override of image names)"
	@echo ""
	@echo "Examples:"
	@echo "  make build VERSION=1.0.0 IMAGE_PREFIX=payments"
	@echo "  make push REGISTRY=ghcr.io/your-org IMAGE_PREFIX=payments VERSION=1.0.0"

.PHONY: build
build: build-orchestrator build-worker build-fe build-agent

.PHONY: build-orchestrator
build-orchestrator:
	$(DOCKER) buildx build \
	  --platform=$(PLATFORM) \
		-f $(ORCH_DOCKERFILE) \
		$(COMMON_BUILD_ARGS)=. \
		-t $(ORCH_IMAGE) \
		$(ORCH_PATH)

.PHONY: build-worker
build-worker:
	$(DOCKER) buildx build \
    --platform=$(PLATFORM) \
		-f $(WORKER_DOCKERFILE) \
		$(COMMON_BUILD_ARGS)=. \
		-t $(WORKER_IMAGE) \
		$(WORKER_PATH)

.PHONY: build-fe
build-fe:
	$(DOCKER) buildx build \
    --platform=$(PLATFORM) \
		-f $(FE_DOCKERFILE) \
		$(COMMON_BUILD_ARGS)=. \
		-t $(FE_IMAGE) \
		$(FE_PATH)

.PHONY: build-agent
build-agent:
	$(DOCKER) buildx build \
    --platform=$(PLATFORM) \
		-f $(AGENT_DOCKERFILE) \
		$(COMMON_BUILD_ARGS)=. \
		-t $(AGENT_IMAGE) \
		$(AGENT_PATH)


.PHONY: push
push: push-orchestrator push-worker push-fe push-agent

.PHONY: push-orchestrator
push-orchestrator:
	@if [ -z "$(REGISTRY)" ] && echo "$(ORCH_IMAGE)" | grep -q '^[^/]\+:'; then \
		echo "WARNING: no REGISTRY set. Push may fail."; \
	fi
	$(DOCKER) push $(ORCH_IMAGE)

.PHONY: push-worker
push-worker:
	@if [ -z "$(REGISTRY)" ] && echo "$(WORKER_IMAGE)" | grep -q '^[^/]\+:'; then \
		echo "WARNING: no REGISTRY set. Push may fail."; \
	fi
	$(DOCKER) push $(WORKER_IMAGE)

.PHONY: push-fe
push-fe:
	@if [ -z "$(REGISTRY)" ] && echo "$(FE_IMAGE)" | grep -q '^[^/]\+:'; then \
		echo "WARNING: no REGISTRY set. Push may fail."; \
	fi
	$(DOCKER) push $(FE_IMAGE)

.PHONY: push-agent
push-agent:
	@if [ -z "$(REGISTRY)" ] && echo "$(AGENT_IMAGE)" | grep -q '^[^/]\+:'; then \
		echo "WARNING: no REGISTRY set. Push may fail."; \
	fi
	$(DOCKER) push $(AGENT_IMAGE)

.PHONY: clean
clean:
	$(DOCKER) rmi $(ORCH_IMAGE) || true
	$(DOCKER) rmi $(WORKER_IMAGE) || true
	$(DOCKER) rmi $(FE_IMAGE) || true
	$(DOCKER) rmi $(AGENT_IMAGE) || true

.PHONY: dev
dev:
	@echo "Starting dev environment"
	$(DOCKER) compose up -d azurite
	bash src/tests/ingest_test_schema.sh localhost:7072
	@set -euo pipefail; \
	trap 'echo "Stopping dev processes..."; kill -9 -P $$; exit 0' INT TERM; \
	( cd $(ORCH_PATH) && FEATURE_DEV=true DEV_SCRIPT_PATH=src/runbooks/ API_PREFIX=/api exec python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 7071 ) & \
	( cd $(WORKER_PATH) && FEATURE_DEV=true DEV_SCRIPT_PATH=src/runbooks/ API_PREFIX=/api exec python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 7072 ) & \
	( cd $(FE_PATH) && API_URL=http://localhost:7071/api exec npm run dev ) & \
	wait

.PHONY: test-env-build
test-env-build:
	docker-compose build

.PHONY: test-env-start
test-env-start:
	docker-compose up -d && sleep 2 && bash src/tests/ingest_test_schema.sh
	@echo "Test with -> curl --location 'http://localhost:7071/api/Trigger?id=test-2' --header 'Content-Type: application/json'"
	@echo "ClouDO UI -> http://localhost:3000"

.PHONY: test-env-stop
test-env-stop:
	docker-compose down

.PHONY: test-env-restart
test-env-restart: test-env-stop test-env-start

.PHONY: test
test: test-orchestrator test-worker test-agent

.PHONY: test-orchestrator
test-orchestrator:
	@echo "Running orchestrator unit tests..."
	cd $(ORCH_PATH) && \
	  ( [ -x .venv/bin/pytest ] || (python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt pytest) ) && \
	  .venv/bin/python -m pytest -q

.PHONY: test-worker
test-worker:
	@echo "Running worker unit tests..."
	cd $(WORKER_PATH) && \
	  ( [ -x .venv/bin/pytest ] || (python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt pytest) ) && \
	  .venv/bin/python -m pytest -q

.PHONY: test-agent
test-agent:
	@echo "Running agent unit tests..."
	cd $(AGENT_PATH) && \
	  ( [ -x .venv/bin/pytest ] || (python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt pytest) ) && \
	  .venv/bin/python -m pytest -q
