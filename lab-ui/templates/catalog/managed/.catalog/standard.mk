# Managed by the Lab catalog standard 5: change it with catalog.py upgrade, in a pull request a maintainer reads.
# The Lab this catalog is written for: a checkout beside this folder, or LAB=/path/to/gravitee-lab
LAB ?= ../gravitee-lab
CATALOG_PY := $(LAB)/lab-ui/catalog.py

.PHONY: dev check ci index upgrade settings release explain

# Open this catalog in the Lab: links the folder into the Lab checkout (idempotent)
dev:
	@python3 -I $(CATALOG_PY) link .

# Every rule CI runs that needs no network: the files, and the commits since origin/main (the items need Docker Compose 5)
check:
	@python3 -I $(CATALOG_PY) check .

# Everything CI runs without a license: check, then build, test and start the apps of what changed since main (ALL=1: everything,
# APP=name: one app)
ci: check
	@python3 -I $(CATALOG_PY) ci run . $(if $(ALL),--all,) $(if $(APP),--app $(APP),)

index:
	@python3 -I $(CATALOG_PY) index . --out catalog.json

# The plan of the managed files (WRITE=1: write them; LAB_REF=commit or latest: move the Lab pin too)
upgrade:
	@python3 -I $(CATALOG_PY) upgrade . $(if $(WRITE),--write,) $(if $(LAB_REF),--lab-ref $(LAB_REF),)

# What GitHub still has to set (APPLY=1: the requests that set it; APPLY=1 YES=1: make them, which needs admin rights)
settings:
	@python3 -I $(CATALOG_PY) repo-settings . $(if $(APPLY),--apply $(if $(YES),--yes,),)

release:
	@python3 -I $(CATALOG_PY) release $(VERSION) $(if $(TAG),--tag,)

explain:
	@python3 -I $(CATALOG_PY) explain $(CODE)
