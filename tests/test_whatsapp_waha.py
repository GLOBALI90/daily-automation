import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import process_leads


def response(status_code, payload=None):
    result = Mock()
    result.status_code = status_code
    result.ok = 200 <= status_code < 400
    result.json.return_value = payload if payload is not None else {}
    if status_code >= 400:
        import requests
        result.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return result


class WahaWhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "WHATSAPP_ENABLED": "true",
            "WAHA_READY": "true",
            "WAHA_URL": "https://waha.example.test",
            "WAHA_SESSION": "default",
            "WAHA_API_KEY": "test-api-key",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_current_check_exists_endpoint_sends_text_to_returned_chat_id(self):
        base = "https://waha.example.test"
        with (
            patch.object(process_leads.requests, "get", side_effect=[
                response(401, {"message": "Unauthorized"}),  # Root page may require auth.
                response(200, {"status": "WORKING"}),         # Initial session check.
                response(200, {"status": "WORKING"}),         # Readiness poll.
                response(200, {
                    "numberExists": True,
                    "chatId": "15551234567@c.us",
                }),
            ]) as get,
            patch.object(process_leads.requests, "post", return_value=response(201, {})) as post,
        ):
            result = process_leads.send_whatsapp(
                "+15551234567", "Hello from a test", attempts=1
            )

        self.assertEqual(result, "sent")
        self.assertTrue(any(
            c.args[0] == f"{base}/api/contacts/check-exists"
            and c.kwargs.get("params") == {"phone": "15551234567", "session": "default"}
            for c in get.call_args_list
        ))
        post.assert_called_once_with(
            f"{base}/api/sendText",
            headers={"Content-Type": "application/json", "X-Api-Key": "test-api-key"},
            json={
                "chatId": "15551234567@c.us",
                "text": "Hello from a test",
                "session": "default",
            },
            timeout=20,
        )

    def test_legacy_endpoint_is_used_only_when_current_endpoint_returns_404(self):
        base = "https://waha.example.test"
        with (
            patch.object(process_leads.requests, "get", side_effect=[
                response(401, {"message": "Unauthorized"}),
                response(200, {"status": "WORKING"}),
                response(200, {"status": "WORKING"}),
                response(404, {"message": "Not found"}),
                response(200, {
                    "numberExists": True,
                    "chatId": "15551234567@c.us",
                }),
            ]) as get,
            patch.object(process_leads.requests, "post", return_value=response(201, {})) as post,
        ):
            result = process_leads.send_whatsapp(
                "+15551234567", "Hello from a legacy test", attempts=1
            )

        self.assertEqual(result, "sent")
        urls = [c.args[0] for c in get.call_args_list]
        self.assertIn(f"{base}/api/contacts/check-exists", urls)
        self.assertIn(f"{base}/api/checkNumberStatus", urls)
        post.assert_called_once()

    def test_number_not_registered_is_not_sent(self):
        with (
            patch.object(process_leads.requests, "get", side_effect=[
                response(401, {"message": "Unauthorized"}),
                response(200, {"status": "WORKING"}),
                response(200, {"status": "WORKING"}),
                response(200, {"numberExists": False}),
            ]),
            patch.object(process_leads.requests, "post") as post,
        ):
            result = process_leads.send_whatsapp(
                "+15551234567", "Do not send", attempts=1
            )

        self.assertEqual(result, "number_not_on_whatsapp")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
