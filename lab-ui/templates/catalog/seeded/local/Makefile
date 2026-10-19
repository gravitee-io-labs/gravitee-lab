# The Lab this catalog is read by: a checkout beside this folder, or LAB=/path/to/gravitee-lab
LAB ?= ../gravitee-lab
CATALOG_PY := $(LAB)/lab-ui/catalog.py

.PHONY: dev check

# Open this catalog in the Lab: links the folder into the Lab checkout (idempotent)
dev:
	@python3 -I "$(CATALOG_PY)" link .

# What a person would see when they add this catalog
check:
	@python3 -I "$(CATALOG_PY)" check .
