# Never built. It names the tools the stages of `catalog.py ci` run as containers, one FROM line for each and named for the
# tool, so that the engine's Dependabot keeps the digest of each current and a reviewer reads a change of one in a pull request.
# lab_ui/catalogs/ci/gitleaks.py reads the FROM line named `gitleaks` and scan.py the one named `trivy`.
FROM ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS gitleaks
FROM ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb AS trivy
