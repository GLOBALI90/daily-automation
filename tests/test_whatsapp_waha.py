import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import process_leads


def response(status_code, payload=None):
    result = Mock()
    result.status_code = status_code
    result.json.return_value = payload if payload is not None else {}
    return result


class WuapiOutboundWhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "WHATSAPP_ENABLED": "true",
            "WUAPI_API_KEY": "test-wuapi-key",
            "WUAPI_ACCOUNT_ID": "test-account-id",
            "GITHUB_RUN_ID": "12345",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_sends_outbound_text_to_e164_number_without_reading_messages(self):
        with (
            patch.object(
                process_leads.requests, "post",
                return_value=response(202, {"id": "msg-test-1", "status": "queued"}),
            ) as post,
            patch.object(process_leads.requests, "get") as get,
        ):
            result = process_leads.send_whatsapp(
                "+1 (555) 123-4567", "Hello from a test", attempts=1
            )

        self.assertEqual(result, "queued")
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.wuapi.dev/v1/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-wuapi-key")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertTrue(kwargs["headers"]["Idempotency-Key"])
        self.assertEqual(kwargs["json"], {
            "accountId": "test-account-id",
            "to": "+15551234567",
            "text": "Hello from a test",
        })
        get.assert_not_called()

    def test_preserves_whatsapp_group_id_and_does_not_read_chats(self):
        with (
            patch.object(
                process_leads.requests, "post",
                return_value=response(202, {"id": "msg-group-1", "status": "queued"}),
            ) as post,
            patch.object(process_leads.requests, "get") as get,
        ):
            result = process_leads.send_whatsapp(
                "120363012345678901@g.us", "Hello group", attempts=1
            )

        self.assertEqual(result, "queued")
        self.assertEqual(post.call_args.kwargs["json"]["to"], "120363012345678901@g.us")
        get.assert_not_called()

    def test_missing_wuapi_credentials_never_sends(self):
        with patch.dict("os.environ", {"WUAPI_API_KEY": "", "WUAPI_ACCOUNT_ID": ""}):
            with patch.object(process_leads.requests, "post") as post:
                result = process_leads.send_whatsapp(
                    "+15551234567", "Do not send", attempts=1
                )

        self.assertEqual(result, "missing_wuapi_config")
        post.assert_not_called()

    def test_generic_phone_is_not_treated_as_verified_whatsapp(self):
        row = {"phone": "+861234567890", "whatsapp": ""}
        self.assertEqual(process_leads.verified_whatsapp_number(row), "")
        row["whatsapp"] = "+861234567890"
        self.assertEqual(
            process_leads.verified_whatsapp_number(row), "+861234567890"
        )

    def test_invalid_non_international_number_never_sends(self):
        with patch.object(process_leads.requests, "post") as post:
            result = process_leads.send_whatsapp(
                "5551234567", "Do not send", attempts=1
            )

        self.assertEqual(result, "skipped_number_not_international")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
