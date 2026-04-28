import unittest
from unittest.mock import patch

from src.api.settings import test_bark
from src.schemas import BarkTestRequest


class SettingsApiTests(unittest.TestCase):
    @patch("src.notify._send_to_key")
    @patch("src.notify._get_bark_config")
    def test_test_bark_prefers_unsaved_form_values(self, mock_get_bark_config, mock_send_to_key):
        mock_get_bark_config.return_value = (["saved-key"], "https://saved.example")
        mock_send_to_key.return_value = True

        result = test_bark(
            body=BarkTestRequest(
                bark_key="temp-key-1\ntemp-key-2",
                bark_server="https://temp.example",
            ),
            db=None,
            _=None,
        )

        self.assertTrue(result["success"])
        self.assertEqual(mock_send_to_key.call_count, 2)
        mock_send_to_key.assert_any_call("temp-key-1", "https://temp.example", unittest.mock.ANY)
        mock_send_to_key.assert_any_call("temp-key-2", "https://temp.example", unittest.mock.ANY)
        mock_get_bark_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
