# Never built. It names the tools the stages of `catalog.py ci` run as containers, one FROM line for each and named for the
# tool, so that the engine's Dependabot keeps the digest of each current and a reviewer reads a change of one in a pull request.
# lab_ui/catalogs/ci/gitleaks.py reads the FROM line named `gitleaks` and scan.py the one named `trivy`; a digest of zeros is the
# one not yet resolved, and the stage refuses to run it (docker buildx imagetools inspect <image> prints the digest to write).
FROM ghcr.io/gitleaks/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854 AS gitleaks
FROM ghcr.io/aquasecurity/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa AS trivy
