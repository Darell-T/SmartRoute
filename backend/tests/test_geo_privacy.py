import unittest
from unittest.mock import Mock, patch

import requests

from app.routers import live_feed
from app.utils import geo


class GeocodePrivacyTests(unittest.TestCase):
    def test_coordinate_input_log_omits_precise_values(self):
        with patch("builtins.print") as printed:
            geo.geocode_address_with_reason("40.7128,-74.0060")
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertNotIn("40.7128", output)
        self.assertNotIn("-74.0060", output)
        self.assertIn("outcome=coordinates", output)

    def test_geocoder_logs_and_errors_omit_address_and_provider_text(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"features": []}
        with patch("requests.get", return_value=response), patch("builtins.print") as printed:
            geo.geocode_address_with_reason("350 5th Ave, New York")
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertNotIn("350 5th", output)
        self.assertIn("outcome=no_result", output)

        with patch("requests.get", side_effect=requests.RequestException("https://provider/?address=350+5th")), patch(
            "builtins.print"
        ) as printed:
            _coords, error = geo.geocode_address_with_reason("350 5th Ave, New York")
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertEqual(error, "Geocoding service is temporarily unavailable.")
        self.assertNotIn("350 5th", output)
        self.assertNotIn("https://", output)
        self.assertIn("error_type=RequestException", output)

    def test_verbose_socket_location_log_omits_precise_input_and_exception_text(self):
        address = "350 5th Ave, New York"
        latitude, longitude = 40.7128, -74.0060
        with patch.dict("os.environ", {"BACKEND_VERBOSE_LOGS": "1"}), patch(
            "builtins.print"
        ) as printed:
            live_feed._vlog(live_feed._location_verbose_log(7, {"Q", "B"}))
            print(live_feed._socket_failure_log("ws_live_feed", requests.RequestException("https://provider/secret")))
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertNotIn(address, output)
        self.assertNotIn(str(latitude), output)
        self.assertNotIn(str(longitude), output)
        self.assertNotIn("https://provider/secret", output)
        self.assertIn("selected_routes=['B', 'Q']", output)
