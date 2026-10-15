import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import social_collector as social
import gemini_utils


class ProviderChainTests(unittest.TestCase):
    def test_scrapecreators_is_only_active_profile_provider(self):
        valid_profile = (
            "Example Auto Parts Company",
            "https://example.cn",
            "Jiangsu, China",
            "Example Auto Parts Company",
            "",
            "",
            "Automotive parts factory and manufacturer in China",
            "120",
            "",
            "https://www.facebook.com/example",
        )

        with (
            patch.object(social, "cloudflare_profile") as cf,
            patch.object(social, "scrapecreators_profile", return_value={"name": "Example Auto Parts Company"}) as sc,
            patch.object(social, "normalize_provider", return_value=valid_profile),
            patch.object(social, "relevant_social_profile", return_value=True),
        ):
            provider, data, errors = social.provider_chain(
                "facebook", "https://www.facebook.com/example"
            )

        self.assertEqual(provider, "ScrapeCreators")
        self.assertTrue(data)
        self.assertEqual(errors, [])
        cf.assert_not_called()
        sc.assert_called_once()
        self.assertFalse(hasattr(social, "socialfetch_profile"))

    def test_auto_falls_back_to_linkedin_when_facebook_has_no_candidates(self):
        linkedin_result = [(
            "https://www.linkedin.com/company/example-chemical",
            "China Jiangsu chemical manufacturer",
            "scrapecreators_linkedin_public_posts_search",
        )]
        with patch.object(social, "discover", side_effect=[[], linkedin_result]) as discover:
            platform, found = social.discover_with_auto_fallback("facebook", True)

        self.assertEqual(platform, "linkedin")
        self.assertEqual(found, linkedin_result)
        self.assertEqual(discover.call_count, 2)
        discover.assert_any_call("facebook")
        discover.assert_any_call("linkedin")

    def test_manual_platform_does_not_switch_platforms(self):
        with patch.object(social, "discover", return_value=[]) as discover:
            platform, found = social.discover_with_auto_fallback("facebook", False)

        self.assertEqual(platform, "facebook")
        self.assertEqual(found, [])
        discover.assert_called_once_with("facebook")

    def test_gemini_retries_primary_then_uses_fallback_model(self):
        class TemporaryServiceError(Exception):
            code = 503

        class Response:
            text = "A short B2B message"

        class Models:
            def __init__(self):
                self.calls = []

            def generate_content(self, *, model, contents):
                self.calls.append(model)
                if len(self.calls) <= 2:
                    raise TemporaryServiceError("temporarily unavailable")
                return Response()

        class Client:
            def __init__(self):
                self.models = Models()

        client = Client()
        with (
            patch.object(gemini_utils.genai, "Client", return_value=client),
            patch.object(gemini_utils.time, "sleep"),
        ):
            result = gemini_utils.generate_text(
                "Write a short message",
                api_key="test-key",
                model="gemini-3.6-flash",
            )

        self.assertEqual(result, "A short B2B message")
        self.assertEqual(
            client.models.calls,
            ["gemini-3.6-flash", "gemini-3.6-flash", "gemini-3.8-flash"],
        )

    def test_linkedin_company_uses_worker_then_company_actor_fallback(self):
        company_url = "https://www.linkedin.com/company/jiangsu-example-chemicals"
        with (
            patch.object(social, "cloudflare_profile") as cf,
            patch.object(
                social, "scrapecreators_profile",
                return_value={"name": "Example Chemicals", "location": "India", "followers": 20},
            ),
            # Reject the wrong-region profile; no paid fallback is attempted.
            patch.object(social, "relevant_social_profile", side_effect=[False]),
        ):
            provider, data, errors = social.provider_chain("linkedin", company_url)

        self.assertEqual(provider, "")
        self.assertIsNone(data)
        self.assertTrue(any("profile_not_relevant_to_target" in e for e in errors))
        cf.assert_not_called()

    def test_canonical_linkedin_url_normalizes_regional_hosts(self):
        self.assertEqual(
            social.canonical_linkedin_url("https://in.linkedin.com/in/example-person?trk=public"),
            "https://www.linkedin.com/in/example-person/",
        )
        self.assertEqual(
            social.canonical_linkedin_url("https://it.linkedin.com/company/example-company/"),
            "https://www.linkedin.com/company/example-company/",
        )

    def test_apify_person_profile_uses_explicit_urls_input(self):
        with patch.object(social, "actor", return_value=[{"name": "Example Person"}]) as actor:
            result = social.apify_profile(
                "linkedin", "https://in.linkedin.com/in/example-person"
            )

        self.assertEqual(result, [{"name": "Example Person"}])
        actor.assert_called_once_with(
            "linkedin",
            {
                "profileScraperMode": "Profile details no email ($4 per 1k)",
                "urls": ["https://www.linkedin.com/in/example-person/"],
            },
        )

    def test_empty_responses_do_not_count_as_success(self):
        with (
            patch.object(social, "cloudflare_profile", return_value={}),
            patch.object(social, "scrapecreators_profile", return_value=[]),
        ):
            provider, data, errors = social.provider_chain(
                "facebook", "https://www.facebook.com/example"
            )

        self.assertEqual(provider, "")
        self.assertIsNone(data)
        self.assertEqual(len(errors), 1)



    def test_facebook_search_query_is_not_counted_as_profile_evidence(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "searchResults": [{
                        "page_name": "Generic Local Page",
                        "page_alias": "genericlocal",
                        "category": "Community",
                    }]
                }

        with (
            patch.object(social, "SCRAPECREATORS", "test-api-key"),
            patch.object(social, "_scrapecreators_get", return_value=Response().json()),
        ):
            candidates = social._facebook_native_candidates(
                "Jiangsu China chemical manufacturer supplier"
            )

        self.assertEqual(len(candidates), 1)
        self.assertNotIn("Jiangsu China chemical manufacturer supplier", candidates[0]["content"])
        self.assertEqual(
            social.social_candidate_score("facebook", candidates[0], candidates[0]["url"]),
            0,
        )

    def test_discovery_can_enrich_auto_parts_factory_when_location_is_missing(self):
        candidate = {
            "title": "Example Auto Parts Manufacturer",
            "content": "Automotive components factory and production supplier",
        }
        score = social.social_candidate_score(
            "linkedin", candidate,
            "https://www.linkedin.com/company/example-auto-parts"
        )
        self.assertGreater(score, 0)


    def test_extract_public_email_and_labeled_whatsapp_from_bio(self):
        email, whatsapp, phone = social.extract_public_contacts(
            "OEM auto parts. Contact: sales@example.cn | WhatsApp: +86 189 9845 3346",
            "",
            "",
        )
        self.assertEqual(email, "sales@example.cn")
        self.assertEqual(whatsapp, "+8618998453346")
        self.assertEqual(phone, "+8618998453346")

    def test_contact_extraction_does_not_treat_unlabeled_number_as_whatsapp(self):
        email, whatsapp, phone = social.extract_public_contacts(
            "Auto parts manufacturer, 6 followers, 2011",
            "",
            "",
        )
        self.assertEqual(email, "")
        self.assertEqual(whatsapp, "")
        self.assertEqual(phone, "")

    def test_linkedin_search_query_is_not_counted_as_profile_evidence(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "success": True,
                    "posts": [{
                        "author": {
                            "url": "https://www.linkedin.com/in/danny-sharma-40b697224",
                            "name": "Danny Sharma",
                        },
                        "description": "A general personal career update unrelated to industrial trade",
                    }],
                }

        # Unit test must not depend on a real provider secret; mock the
        # configured API key and HTTP response so it tests ranking only.
        with (
            patch.object(social, "SCRAPECREATORS", "test-api-key"),
            patch.object(social.session, "get", return_value=Response()) as mocked_get,
        ):
            results = social._linkedin_provider_candidates(
                "China Jiangsu chemical petrochemical procurement purchasing manager importer"
            )
        mocked_get.assert_called_once()

        self.assertEqual(len(results), 1)
        self.assertNotIn("China Jiangsu chemical petrochemical procurement purchasing manager importer", results[0]["content"])
        self.assertEqual(social.social_candidate_score("linkedin", results[0], results[0]["url"]), 0)


    def test_discover_returns_multiple_ranked_candidates_across_queries(self):
        first = {
            "url": "https://www.linkedin.com/company/jiangsu-auto-parts-maker",
            "title": "Jiangsu Auto Parts Factory",
            "content": "Automotive components manufacturer and factory in Jiangsu China",
        }
        second = {
            "url": "https://www.linkedin.com/company/china-engine-components",
            "title": "China Engine Components Manufacturer",
            "content": "Chinese engine parts factory and automotive components production",
        }
        with (
            patch.object(social, "NATIVE_QUERIES", {"linkedin": ["query one", "query two"]}),
            patch.object(social, "native_candidates", side_effect=[[first], [second]]),
        ):
            found = social.discover("linkedin")

        self.assertEqual(len(found), 2)
        self.assertEqual({item[0] for item in found}, {first["url"], second["url"]})

    def test_cloudflare_worker_request_cap_is_enforced_per_run(self):
        with (
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", social.MAX_CLOUDFLARE_CALLS_PER_RUN),
            patch.object(social.session, "post") as mocked_post,
        ):
            with self.assertRaisesRegex(RuntimeError, "cloudflare_per_run_cap_reached"):
                social.cloudflare_profile("facebook", "https://www.facebook.com/example")

        mocked_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
