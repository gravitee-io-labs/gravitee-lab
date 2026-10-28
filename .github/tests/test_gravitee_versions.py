"""The check of the Gravitee releases newer than the defaults of the core (.github/scripts/gravitee_versions.py): what it reads
of the compose file and of the tags, without the network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gravitee_versions  # noqa: E402

COMPOSE = """
  api:
    image: ${APIM_REGISTRY:-graviteeio}/apim-management-api:${APIM_VERSION:-4.13.1}
  ui:
    image: ${APIM_REGISTRY:-graviteeio}/apim-management-ui:${APIM_VERSION:-4.13.1}
  gateway:
    image: ${APIM_GATEWAY_REGISTRY:-${APIM_REGISTRY:-graviteeio}}/apim-gateway:${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4.13.1}-debian}
  am:
    image: ${AM_REGISTRY:-graviteeio}/am-management-api:${AM_VERSION:-4.12.7}
  mongodb:
    image: mongo:${MONGODB_VERSION:-8.0.34}
"""


class GraviteeVersions(unittest.TestCase):
    def test_a_default_is_the_version_of_an_image_of_gravitee_with_the_repository_of_the_first_image_that_names_it(self):
        self.assertEqual(gravitee_versions.defaults(COMPOSE),
                         {"APIM_VERSION": ("4.13.1", "apim-management-api"), "AM_VERSION": ("4.12.7", "am-management-api")})

    def test_the_newest_is_the_highest_release_and_never_a_pre_release_a_line_or_a_system(self):
        self.assertEqual(gravitee_versions.newest(["4.13.1-debian", "4.13", "4.12.10", "4.14.0-alpha.1", "latest", "4.9.30"]),
                         "4.12.10")
        self.assertIsNone(gravitee_versions.newest(["latest", "4.14.0-alpha.1"]))

    def test_only_a_default_that_a_newer_release_passes_is_said(self):
        tags = {"apim-management-api": ["4.13.1", "4.13.0"], "am-management-api": ["4.13.1", "4.12.10", "4.12.7"]}
        self.assertEqual(gravitee_versions.newer(gravitee_versions.defaults(COMPOSE), tags.get),
                         [("AM_VERSION", "4.12.7", "4.13.1")])


if __name__ == "__main__":
    unittest.main()
