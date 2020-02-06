SHELL := bash
PYTHON_NAME = rhasspywake_porcupine_hermes
PACKAGE_NAME = rhasspy-wake-porcupine-hermes
SOURCE = $(PYTHON_NAME)
PYTHON_FILES = $(SOURCE)/*.py bin/publish_wav.py *.py
SHELL_FILES = bin/$(PACKAGE_NAME) debian/bin/* *.sh
PIP_INSTALL ?= install

.PHONY: reformat check dist venv test pyinstaller debian docker deploy

version := $(shell cat VERSION)
architecture := $(shell bash architecture.sh)

debian_package := $(PACKAGE_NAME)_$(version)_$(architecture)
debian_dir := debian/$(debian_package)

# -----------------------------------------------------------------------------
# Python
# -----------------------------------------------------------------------------

reformat:
	scripts/format-code.sh $(PYTHON_FILES)

check:
	scripts/check-code.sh $(PYTHON_FILES)

venv:
	scripts/create-venv.sh

dist: sdist debian

sdist:
	python3 setup.py sdist

test:
	echo "Skipping tests for now"

test-wavs:
	bash etc/test/test_wavs.sh

# -----------------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------------

docker: pyinstaller
	docker build . -t "rhasspy/$(PACKAGE_NAME):$(version)" -t "rhasspy/$(PACKAGE_NAME):latest"

deploy:
	echo "$$DOCKER_PASSWORD" | docker login -u "$$DOCKER_USERNAME" --password-stdin
	docker push "rhasspy/$(PACKAGE_NAME):$(version)"

# -----------------------------------------------------------------------------
# Debian
# -----------------------------------------------------------------------------

pyinstaller:
	scripts/build-pyinstaller.sh "${architecture}" "${version}"

debian:
	scripts/build-debian.sh "${architecture}" "${version}"
