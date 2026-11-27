"""The commands that read catalog units, shared by catalog-template and engine-workflow coverage tests."""

import re

READS_UNITS = re.compile(
    r"\b(?:catalog\.py\s+(?:check|index|link|ci\s+(?:plan|manifests|build|test|smoke|scan-built|"
    r"scan-published|publish-plan|publish|release-notes|e2e-plan|versions-plan|versions-record|versions-release))\b|"
    r"e2e_matrix\.py|image_manifests\.py|server\.py\s+--e2e)"
)
