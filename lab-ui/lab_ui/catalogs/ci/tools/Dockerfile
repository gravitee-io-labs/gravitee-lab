# Never built. It names the tools the stages of `catalog.py ci` run as containers, one FROM line for each and named for the
# tool, so that the engine's Dependabot keeps the digest of each current and a reviewer reads a change of one in a pull request.
# lab_ui/catalogs/ci/gitleaks.py reads the FROM line named `gitleaks` and scan.py the one named `trivy`; a digest of zeros is the
# one not yet resolved, and the stage refuses to run it (docker buildx imagetools inspect <image> prints the digest to write).
FROM ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS gitleaks
FROM ghcr.io/aquasecurity/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c AS trivy
