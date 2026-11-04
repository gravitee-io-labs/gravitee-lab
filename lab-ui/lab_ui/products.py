"""The names of Gravitee's products and of their images that the engine knows itself: the one place of them in lab_ui/
(AGENTS.md). Everything else is read from the Compose files: the x-gravitee blocks, the labels, the includes, the image lines.
What a seed fills is the seeder's own layout (seeder/README.md): its folders are a contract with the seeder."""

import re

# The systems an image is built on, at the end of its tag: not part of its version (versions.bare). js/core.js holds the
# same list for the page, and a test keeps them equal
VARIANTS = ("chainguard-fips", "chainguard", "debian", "noble", "jammy", "alpine")

# The products, as the groups of the core name them; Gamma is what the plan of a Cloud account enables (its subscription
# says gammaEnabled), which no gateway scope names
APIM, AM, GAMMA = "API Management", "Access Management", "Gamma"
# The folders of a playground's seed/, in the seeder's layout, and the product each fills, in the order a reason lists them
SEEDED = {"apim": APIM, "am": AM, "aim": GAMMA, "authz": GAMMA}
# What a deploy to a platform seeds: the folder of API Management there, and here those of Access Management and OpenFGA,
# which run on this machine whichever platform runs API Management, as for a start here
DEPLOYED, SEEDED_HERE = "apim", ("am", "openfga")
LOCAL_PRODUCTS = (AM,)   # the products that a platform does not have to run: they run here
APIM_FEATURES = "apim-"   # how the license features of API Management begin: those a platform that runs it must have

# The scope of a gateway, as the Cloud says it, and the id the project gives that product (its plugins folder, the
# `product` of a group of the core)
SCOPES = {"APIM": "apim", "AM": "am"}
GATEWAY = "apim-gateway"   # the image of API Management's gateway: the service of the core that runs it is this machine's gateway

AZURE = re.compile(r"[a-z0-9]+\.azurecr\.io")   # an Azure registry, which Docker pulls from once the Azure CLI logged it in
GHCR = "ghcr.io"   # GitHub's registry, which Docker pulls from once the GitHub CLI logged it in
