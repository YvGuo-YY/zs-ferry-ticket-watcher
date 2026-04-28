import unittest
from unittest.mock import patch

from src.crawler.api_backend import ApiBackend


class ApiBackendTripQueryModeTests(unittest.TestCase):
    def test_passenger_query_uses_ship_endpoint(self):
        backend = ApiBackend()

        with patch.object(ApiBackend, "_authed_post", return_value={"code": 200, "data": []}) as mocked:
            result = backend.query_trips(object(), object(), 1010, 1017, "2026-05-04", require_vehicle=False)

        self.assertEqual(result, [])
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        self.assertEqual(args[0], "/line/ship/enq")
        self.assertEqual(kwargs["data"], {
            "accountTypeId": "0",
            "startPortNo": 1010,
            "endPortNo": 1017,
            "startDate": "2026-05-04",
        })

    def test_vehicle_query_uses_ferry_endpoint(self):
        backend = ApiBackend()

        with patch.object(ApiBackend, "_authed_post", return_value={"code": 200, "data": []}) as mocked:
            result = backend.query_trips(object(), object(), 1010, 1028, "2026-05-03", require_vehicle=True)

        self.assertEqual(result, [])
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        self.assertEqual(args[0], "/line/ferry/enq")
        self.assertEqual(kwargs["data"], {
            "startPortNo": 1010,
            "endPortNo": 1028,
            "startDate": "2026-05-03",
        })


if __name__ == "__main__":
    unittest.main()
