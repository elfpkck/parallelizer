QGIS_VERSION ?= $(shell sed -n 's|^ARG QGIS_IMAGE=qgis/qgis:||p' Dockerfile)
IMAGE := qgis-for-pptl:$(QGIS_VERSION)
CONTAINER := qgis_pptl

.PHONY: build run install install-dev test test-coverage test-perf test-all stop clean tag

define FINALIZE_CHANGELOG
import os, pathlib, datetime, sys
v = os.environ['VERSION']
p = pathlib.Path('CHANGELOG.md')
lines = p.read_text().splitlines()
header = '## [Unreleased]'
try:
    i = lines.index(header)
except ValueError:
    sys.exit('No [Unreleased] section in CHANGELOG.md')
j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith('## [')), len(lines))
if not [l for l in lines[i + 1:j] if l.strip()]:
    sys.exit('[Unreleased] has no entries; add some before tagging')
lines[i:i + 1] = [header, '', f'## [{v}] - {datetime.date.today().isoformat()}']
p.write_text('\n'.join(lines) + '\n')
endef
export FINALIZE_CHANGELOG

build:
	DOCKER_SCAN_SUGGEST=false docker build --build-arg QGIS_IMAGE=qgis/qgis:$(QGIS_VERSION) -t $(IMAGE) -f Dockerfile .

# -v /pptl/.venv - an anonymous volume, which "shadows" the host's `.venv` directory
# --rm: container is auto-removed on stop so `make run` is idempotent across sessions
run: build
	docker run -d --rm --name $(CONTAINER) \
		-v "$(CURDIR):/pptl" \
		-v /pptl/.venv \
		-e PYTHONPATH=/pptl:/usr/share/qgis/python \
		$(IMAGE)

install:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv venv --allow-existing --system-site-packages && uv sync --locked --no-dev"

# For an unknown reason, I need to add the pydevd package in this specific way to make the PyCharm remote debugger work with Docker
install-dev:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv venv --allow-existing --system-site-packages && uv sync --locked && uv pip install pydevd"

test:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv run --no-dev pytest /pptl/tests --qgis_disable_gui"

test-coverage:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv run --no-dev pytest /pptl/tests \
		--qgis_disable_gui \
		--cov=/pptl/PolygonsParallelToLine/src \
		--cov-report=term-missing:skip-covered \
		--cov-report=xml:/pptl/coverage.xml \
		--junitxml=/pptl/junit.xml -o junit_family=legacy"

test-perf:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv run --no-dev pytest /pptl/tests -m perf --qgis_disable_gui"

test-all:
	docker exec $(CONTAINER) sh -c "cd /pptl && uv run --no-dev pytest /pptl/tests -o 'addopts=' --qgis_disable_gui"

stop:
	-docker stop $(CONTAINER)

clean: stop
	-docker rm $(CONTAINER)

tag:
	@test -n "$(VERSION)" || { echo "Usage: make tag VERSION=X.Y.Z" >&2; exit 2; }
	@echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-.+)?$$' \
		|| { echo "VERSION must be N.N.N or N.N.N-suffix" >&2; exit 2; }
	@branch=$$(git rev-parse --abbrev-ref HEAD); test "$$branch" = "main" \
		|| { echo "Must be on main; currently on $$branch" >&2; exit 2; }
	@git diff --quiet && git diff --cached --quiet \
		|| { echo "Working tree not clean; commit or stash first" >&2; exit 2; }
	@! git rev-parse --verify --quiet "refs/tags/$(VERSION)" >/dev/null \
		|| { echo "Tag $(VERSION) already exists" >&2; exit 2; }
	@VERSION="$(VERSION)" python3 -c "$$FINALIZE_CHANGELOG"
	git add CHANGELOG.md
	git commit -m "Update CHANGELOG for version $(VERSION) release"
	git tag "$(VERSION)"
	git push --atomic origin HEAD "$(VERSION)"
