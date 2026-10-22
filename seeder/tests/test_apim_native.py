"""An API that has no path is still found again: a native Kafka API is reached by the host of its Kafka listener."""

import unittest

from lab_seeder import apim


class AddressTest(unittest.TestCase):
    def test_a_proxy_api_is_reached_by_its_first_path(self):
        api = {"listeners": [{"type": "HTTP", "paths": [{"path": "/orders/"}, {"path": "/other/"}]}]}
        self.assertEqual(apim.ApiManagement.address(api), "/orders/")

    def test_a_native_kafka_api_is_reached_by_the_host_of_its_listener(self):
        api = {"listeners": [{"type": "KAFKA", "host": "orders", "entrypoints": [{"type": "native-kafka"}]}]}
        self.assertEqual(apim.ApiManagement.address(api), "kafka://orders")

    def test_two_kafka_apis_with_two_hosts_are_two_addresses(self):
        one = {"listeners": [{"type": "KAFKA", "host": "orders"}]}
        two = {"listeners": [{"type": "KAFKA", "host": "payments"}]}
        self.assertNotEqual(apim.ApiManagement.address(one), apim.ApiManagement.address(two))

    def test_an_api_with_nothing_to_reach_it_by_has_no_address(self):
        self.assertIsNone(apim.ApiManagement.address({"listeners": []}))
        self.assertIsNone(apim.ApiManagement.address({"listeners": [{"type": "SUBSCRIPTION"}]}))


if __name__ == "__main__":
    unittest.main()
