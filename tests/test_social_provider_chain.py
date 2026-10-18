import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import social_collector as social
import gemini_utils


class ProviderChainTests(unittest.TestCase):
    def test_composio_search_resolves_rest_account_and_owner(self):
        class Response:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

            def raise_for_status(self):
                return None

        account_response = Response({"items": [{
            "id": "ca_test-firecrawl",
            "status": "ACTIVE",
            "toolkit": {"slug": "firecrawl"},
            "user_id": "composio-owner-123",
            "is_default": True,
        }]})
        execute_response = Response({"successful": True, "data": {"results": []}})

        with (
            patch.object(social, "COMPOSIO_API_KEY", "test-composio-key"),
            patch.object(social, "COMPOSIO_FIRECRAWL_CONNECTED_ACCOUNT_ID", "firecrawl_test-alias"),
            patch.object(social, "COMPOSIO_USER_ID", "GLOBALI90"),
            patch.object(social.session, "get", return_value=account_response),
            patch.object(social.session, "post", return_value=execute_response) as post,
        ):
            result = social.composio_search_candidates("linkedin", "test query")

        self.assertEqual(result, [])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["user_id"], "composio-owner-123")
        self.assertEqual(body["connected_account_id"], "ca_test-firecrawl")
        self.assertEqual(body["arguments"]["q"].startswith("site:linkedin.com/company"), True)

    def test_product_first_query_uses_configured_product_market_and_buyers(self):
        class Response:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

            def raise_for_status(self):
                return None

        account_response = Response({"items": [{
            "id": "ca_test-firecrawl",
            "status": "ACTIVE",
            "toolkit": {"slug": "firecrawl"},
            "user_id": "composio-owner-123",
        }]})
        execute_response = Response({"successful": True, "data": {"results": []}})
        with (
            patch.object(social, "COMPOSIO_API_KEY", "test-composio-key"),
            patch.object(social, "COMPOSIO_FIRECRAWL_CONNECTED_ACCOUNT_ID", "firecrawl_test-alias"),
            patch.object(social, "COMPOSIO_USER_ID", "GLOBALI90"),
            patch.object(social, "PRODUCT_NAME", "titanium dioxide"),
            patch.object(social, "TARGET_MARKET", "Turkey"),
            patch.object(social, "BUYER_TYPES", "importers, distributors"),
            patch.object(social.session, "get", return_value=account_response),
            patch.object(social.session, "post", return_value=execute_response) as post,
        ):
            social.composio_search_candidates("linkedin", "unused")

        query = post.call_args.kwargs["json"]["arguments"]["q"]
        self.assertIn("titanium dioxide", query)
        self.assertIn("Turkey", query)
        self.assertIn("importers, distributors", query)

    def test_dynamic_product_and_market_relevance(self):
        with (
            patch.object(social, "PRODUCT_TERMS", ("titanium dioxide", "titanium")),
            patch.object(social, "MARKET_SIGNALS", ("turkey",)),
            patch.object(social, "BUYER_TYPE_TERMS", ("importer", "distributor")),
            patch.object(social, "TARGET_MARKET", "Turkey"),
        ):
            self.assertTrue(social.relevant_social_profile(
                "linkedin",
                "https://www.linkedin.com/company/example",
                "Example Titanium Dioxide Importer",
                "https://example.com",
                "Istanbul, Turkey",
                "Importer and distributor of titanium dioxide",
            ))

    def test_facebook_profile_enrichment_uses_cloudflare_only(self):
        valid_profile = (
            "Example Industrial Lubricants Company",
            "https://example.cn",
            "Jiangsu, China",
            "Example Industrial Lubricants Company",
            "",
            "",
            "Industrial lubricants factory and manufacturer in China",
            "120",
            "",
            "https://www.facebook.com/example",
        )

        with (
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", 0),
            patch.object(social, "cloudflare_profile", return_value={"name": "Example Industrial Lubricants Company"}) as cf,
            patch.object(social, "scrapecreators_profile") as sc,
            patch.object(social, "normalize_provider", return_value=valid_profile),
            patch.object(social, "relevant_social_profile", return_value=True),
        ):
            provider, data, errors = social.provider_chain(
                "facebook", "https://www.facebook.com/example"
            )

        self.assertEqual(provider, "CloudflareDeepBackup")
        self.assertTrue(data)
        self.assertEqual(errors, [])
        cf.assert_called_once()
        sc.assert_not_called()

    def test_brave_search_backup_parses_only_public_facebook_pages(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"web": {"results": [
                    {
                        "url": "https://www.facebook.com/jiangsuindustriallubricants/",
                        "title": "Jiangsu Industrial Lubricants Manufacturer",
                        "description": "Factory producing industrial lubricating oil in Jiangsu, China",
                    },
                    {
                        "url": "https://www.facebook.com/groups/lubricants/",
                        "title": "Industrial lubricants group",
                        "description": "China factory",
                    },
                ]}}

        with (
            patch.object(social, "BRAVE_SEARCH_API_KEY", "test-brave-key"),
            patch.object(social.session, "get", return_value=Response()) as get,
        ):
            candidates = social._brave_search_candidates("facebook", "ignored query")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["search_provider"], "Brave Search API")
        self.assertGreater(
            social.social_candidate_score("facebook", candidates[0], candidates[0]["url"]),
            0,
        )
        self.assertEqual(
            get.call_args.args[0],
            "https://api.search.brave.com/res/v1/web/search",
        )
        self.assertEqual(
            get.call_args.kwargs["headers"]["X-Subscription-Token"],
            "test-brave-key",
        )

    def test_brave_search_backup_is_skipped_without_key(self):
        with (
            patch.object(social, "BRAVE_SEARCH_API_KEY", ""),
            patch.object(social.session, "get") as get,
        ):
            self.assertEqual(
                social._brave_search_candidates("facebook", "industrial lubricants"),
                [],
            )
        get.assert_not_called()

    def test_linkedin_profile_enrichment_uses_apify_only(self):
        url = "https://www.linkedin.com/company/example-lubricants"
        valid_profile = (
            "Example Industrial Lubricants", "https://example.cn", "Jiangsu, China",
            "Example Industrial Lubricants", "", "", "Industrial lubricants manufacturer factory in China",
            "100", "", url,
        )
        with (
            patch.object(social, "APIFY", "test-apify-token"),
            patch.object(social, "apify_profile", return_value=[{"name": "Example Industrial Lubricants"}]) as apify,
            patch.object(social, "cloudflare_profile") as cf,
            patch.object(social, "normalize_provider", return_value=valid_profile),
            patch.object(social, "relevant_social_profile", return_value=True),
        ):
            provider, data, errors = social.provider_chain("linkedin", url)
        self.assertEqual(provider, "Apify")
        self.assertTrue(data)
        self.assertEqual(errors, [])
        apify.assert_called_once_with("linkedin", url)
        cf.assert_not_called()

    def test_cloudflare_search_fallback_runs_when_native_discovery_is_empty(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "success": True,
                    "results": [{
                        "platform": "facebook",
                        "candidates": [
                            "https://www.facebook.com/example-lubricants"
                        ],
                        "candidate_count": 1,
                    }],
                }

        with (
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", 0),
            patch.object(social, "CLOUDFLARE_SEARCH_USED_THIS_RUN", False),
            patch.object(social, "LAST_CLOUDFLARE_REQUEST_AT", 0.0),
            patch.object(social, "native_candidates", return_value=[]),
            patch.object(social.session, "post", return_value=Response()) as post,
        ):
            found = social.discover("facebook")

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "https://www.facebook.com/example-lubricants")
        self.assertEqual(found[0][2], "cloudflare_browser_search_fallback")
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith("/search"))
        self.assertEqual(post.call_args.kwargs["json"]["platform"], "facebook")

    def test_auto_falls_back_to_linkedin_when_facebook_has_no_candidates(self):
        linkedin_result = [(
            "https://www.linkedin.com/company/example-chemical",
            "China Jiangsu chemical manufacturer",
            "apify_linkedin_company_search",
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

    def test_irrelevant_apify_profile_does_not_use_cloudflare(self):
        company_url = "https://www.linkedin.com/company/jiangsu-example-chemicals"
        with (
            patch.object(social, "APIFY", "test-apify-token"),
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", 0),
            patch.object(social, "cloudflare_profile") as cf,
            patch.object(social, "apify_profile", return_value={"name": "Example Chemicals"}),
            patch.object(social, "normalize_provider", return_value=(
                "Example Chemicals", "", "India", "Example Chemicals", "", "",
                "Industrial lubricant manufacturer in India", "20", "",
                company_url,
            )),
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

    def test_facebook_uses_one_bounded_google_fallback_for_public_pages(self):
        ad_library_empty = {"searchResults": []}
        google_results = {
            "results": [{
                "url": "https://www.facebook.com/jiangsuindustriallubricants",
                "title": "Jiangsu Industrial Lubricants Manufacturer",
                "description": "Factory producing industrial lubricating oil in Jiangsu, China",
            }]
        }
        with (
            patch.object(social, "SCRAPECREATORS", "test-api-key"),
            patch.object(social, "FACEBOOK_GOOGLE_SEARCH_USED_THIS_RUN", False),
            patch.object(
                social, "_scrapecreators_get",
                side_effect=[ad_library_empty, google_results, ad_library_empty],
            ) as mocked_get,
        ):
            first = social._facebook_native_candidates("industrial lubricant manufacturer")
            second = social._facebook_native_candidates("industrial lubricant manufacturer")

        self.assertEqual(len(first), 1)
        self.assertIn("facebook.com", first[0]["url"])
        self.assertGreater(
            social.social_candidate_score("facebook", first[0], first[0]["url"]), 0
        )
        self.assertEqual(second, [])
        paths = [call.args[0] for call in mocked_get.call_args_list]
        self.assertEqual(paths.count("/v1/google/search"), 1)

    def test_discovery_rejects_candidates_without_china_evidence(self):
        candidate = {
            "title": "Example Industrial Lubricants Manufacturer",
            "content": "Automotive components factory and production supplier",
        }
        score = social.social_candidate_score(
            "linkedin", candidate,
            "https://www.linkedin.com/company/example-auto-parts"
        )
        self.assertEqual(score, 0)

    def test_linkedin_search_http_404_is_treated_as_empty_results(self):
        class Response:
            status_code = 404
            def raise_for_status(self):
                import requests
                raise requests.HTTPError("404 Client Error")

        with (
            patch.object(social, "SCRAPECREATORS", "test-api-key"),
            patch.object(social.session, "get", return_value=Response()),
        ):
            results = social._linkedin_provider_candidates("short query with no matches")
        self.assertEqual(results, [])

    def test_personal_profile_role_must_be_in_author_metadata_not_post_text(self):
        candidate = {
            "title": "Li Wei",
            "content": "China industrial lubricant procurement manager wanted; contact our team",
            "headline": "Operations specialist",
            "current_company": "Jiangsu Industrial Lubricants Manufacturer",
            "location": "Jiangsu, China",
        }
        self.assertEqual(
            social.personal_profile_candidate_score(
                candidate, "https://www.linkedin.com/in/li-wei"
            ),
            0,
        )

    def test_empty_run_creates_personal_profiles_csv_header(self):
        import csv
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personal_profiles.csv"
            with (
                patch.object(social, "ROOT", Path(tmp)),
                patch.object(social, "PERSONAL_PROFILES", path),
                patch.object(social, "PLATFORM", "facebook"),
                patch.object(social, "PLATFORMS", ["linkedin", "facebook"]),
                patch.object(social, "AUTO_PLATFORMS", ["facebook"]),
                patch.object(social, "discover", return_value=[]),
                patch.object(social, "write_run_report"),
            ):
                social.main()
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                self.assertEqual(next(reader), social.PERSONAL_PROFILE_FIELDS)
                self.assertEqual(list(reader), [])

    def test_relevant_public_linkedin_personal_profile_is_accepted_for_account_only_list(self):
        candidate = {
            "title": "Li Wei",
            "content": "Procurement manager at Jiangsu industrial lubricants manufacturer in China",
            "headline": "Procurement Manager",
            "current_company": "Jiangsu Industrial Lubricants Manufacturer",
            "location": "Jiangsu, China",
        }
        url = "https://www.linkedin.com/in/li-wei-procurement"
        self.assertGreater(social.personal_profile_candidate_score(candidate, url), 0)
        self.assertEqual(social.social_candidate_score("linkedin", candidate, url),
                         social.personal_profile_candidate_score(candidate, url))

    def test_personal_profile_storage_contains_no_contact_fields(self):
        import csv
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personal_profiles.csv"
            with (
                patch.object(social, "PERSONAL_PROFILES", path),
                patch.object(social, "RUN_ID", "test-run"),
                patch.object(social, "NOW", "2026-10-16T10:00:00+00:00"),
            ):
                saved = social.save_personal_profile(
                    "linkedin", "https://www.linkedin.com/in/li-wei-procurement",
                    "Li Wei"
                )
            self.assertTrue(saved)
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["profile_url"], "https://www.linkedin.com/in/li-wei-procurement")
            self.assertEqual(rows[0]["public_name"], "Li Wei")
            self.assertEqual(set(rows[0].keys()), set(social.PERSONAL_PROFILE_FIELDS))
            self.assertNotIn("email", rows[0])
            self.assertNotIn("phone", rows[0])
            self.assertNotIn("whatsapp", rows[0])

    def test_discovery_accepts_china_lubricant_manufacturer_evidence(self):
        candidate = {
            "title": "Jiangsu Industrial Lubricants Factory",
            "content": "Industrial lubricant manufacturer and production factory in Jiangsu, China",
        }
        score = social.social_candidate_score(
            "linkedin", candidate,
            "https://www.linkedin.com/company/jiangsu-lubricants"
        )
        self.assertGreater(score, 0)

    def test_worker_search_fallback_tops_up_partial_native_discovery(self):
        native = {
            "url": "https://www.linkedin.com/company/jiangsu-lubricants",
            "title": "Jiangsu Industrial Lubricants Factory",
            "content": "Industrial lubricant manufacturer in Jiangsu China",
        }
        class Response:
            status_code = 200
            text = ""
            def json(self):
                return {"success": True, "results": [{
                    "platform": "linkedin",
                    "candidates": ["https://www.linkedin.com/company/another-lubricant"],
                }]}

        with (
            patch.object(social, "NATIVE_QUERIES", {"linkedin": ["query one"]}),
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", 0),
            patch.object(social, "CLOUDFLARE_SEARCH_USED_THIS_RUN", False),
            patch.object(social, "LAST_CLOUDFLARE_REQUEST_AT", 0.0),
            patch.object(social, "native_candidates", return_value=[native]),
            patch.object(social.session, "post", return_value=Response()) as post,
        ):
            found = social.discover("linkedin")

        self.assertEqual(len(found), 2)
        self.assertTrue(any(item[0].endswith("another-lubricant") for item in found))
        post.assert_called_once()


    def test_extract_public_email_and_labeled_whatsapp_from_bio(self):
        email, whatsapp, phone = social.extract_public_contacts(
            "OEM industrial lubricants. Contact: sales@example.cn | WhatsApp: +86 189 9845 3346",
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
                            "url": "https://www.linkedin.com/company/example-industrial-lubricants",
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
            "title": "Jiangsu Industrial Lubricants Factory",
            "content": "Automotive components manufacturer and factory in Jiangsu China",
        }
        second = {
            "url": "https://www.linkedin.com/company/china-engine-components",
            "title": "China Engine Components Manufacturer",
            "content": "Chinese lubricating oils factory and industrial lubricants production",
        }
        with (
            patch.object(social, "NATIVE_QUERIES", {"linkedin": ["query one", "query two"]}),
            patch.object(social, "native_candidates", side_effect=[[first], [second]]),
        ):
            found = social.discover("linkedin")

        self.assertEqual(len(found), 2)
        self.assertEqual({item[0] for item in found}, {first["url"], second["url"]})

    def test_cloudflare_primary_search_supports_both_platforms(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "success": True,
                    "results": [
                        {"platform": "linkedin", "candidates": [
                            "https://www.linkedin.com/company/example-lubricants"
                        ]},
                        {"platform": "facebook", "candidates": [
                            "https://www.facebook.com/examplelubricants"
                        ]},
                    ],
                }

        with (
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", 0),
            patch.object(social, "CLOUDFLARE_SEARCH_USED_THIS_RUN", False),
            patch.object(social, "LAST_CLOUDFLARE_REQUEST_AT", 0.0),
            patch.object(social.session, "post", return_value=Response()) as post,
        ):
            found = social.cloudflare_search_candidates("both", "China industrial lubricant")

        self.assertEqual({item["platform"] for item in found}, {"linkedin", "facebook"})
        self.assertEqual(len(found), 2)
        self.assertEqual(post.call_args.kwargs["json"]["platform"], "both")

    def test_discover_uses_cloudflare_primary_and_skips_known_urls_before_enrichment(self):
        known = "https://www.linkedin.com/company/known-lubricants"
        new = "https://www.linkedin.com/company/new-lubricants"
        with (
            patch.object(social, "CLOUDFLARE_PRIMARY_RESULTS", {
                "linkedin": [
                    {"url": known, "platform": "linkedin"},
                    {"url": new, "platform": "linkedin"},
                ],
                "facebook": [],
            }),
            patch.object(social, "EXISTING_SOCIAL_KEYS", {
                ("linkedin", social.canonical_social_key("linkedin", known))
            }),
            patch.object(social, "EXISTING_PERSONAL_KEYS", set()),
            patch.object(social, "NATIVE_QUERIES", {"linkedin": []}),
            patch.object(social, "DUPLICATE_PREFILTER_SKIPPED", 0),
        ):
            found = social.discover("linkedin")

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], new)
        self.assertEqual(found[0][2], "cloudflare_browser_search_primary")

    def test_cloudflare_worker_request_cap_is_enforced_per_run(self):
        with (
            patch.object(social, "CLOUDFLARE_TOKEN", "test-token"),
            patch.object(social, "CLOUDFLARE_CALLS_THIS_RUN", social.MAX_CLOUDFLARE_CALLS_PER_RUN),
            patch.object(social.session, "post") as mocked_post,
        ):
            with self.assertRaisesRegex(RuntimeError, "cloudflare_per_run_cap_reached"):
                social.cloudflare_profile("facebook", "https://www.facebook.com/example")

        mocked_post.assert_not_called()


    def test_product_web_collection_uses_one_search_and_extracts_public_contacts(self):
        import csv
        import tempfile
        class Response:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {"success": True, "data": {"web": [{
                    "url": "https://example-manufacturer.cn/contact",
                    "title": "Example Industrial Supplier | Contact",
                    "description": "Industrial lubricants manufacturer. Contact sales@example-manufacturer.cn Tel +86 123 4567 8901",
                }, {
                    "url": "https://www.linkedin.com/company/not-an-official-site",
                    "title": "Social profile",
                    "description": "Should be filtered",
                }]}}
        with tempfile.TemporaryDirectory() as tmp:
            leads_path = Path(tmp) / "product_leads.csv"
            with (
                patch.object(social, "FIRECRAWL_API_KEY", "test-firecrawl-key"),
                patch.object(social, "PRODUCT_LEADS", leads_path),
                patch.object(social, "PRODUCT_NAME", "industrial lubricants"),
                patch.object(social, "TARGET_MARKET", "China"),
                patch.object(social, "BUYER_TYPES", "manufacturers, importers"),
                patch.object(social, "MAX_ITEMS", 3),
                patch.object(social, "GITHUB_RUN_NUMBER", 0),
                patch.object(social, "FIRECRAWL_SEARCHES_THIS_RUN", 0),
                patch.object(social, "COMPOSIO_API_KEY", ""),
                patch.object(social, "qualify_product_lead_with_ai", return_value={
                    "ai_customer_fit": "likely_customer",
                    "ai_confidence": "88",
                    "ai_recommended_buyer_type": "importer",
                    "ai_reason": "Evidence indicates a possible customer.",
                    "ai_review_status": "pending_manual_review",
                }),
                patch.object(social.session, "post", return_value=Response()) as post,
            ):
                social.run_product_web_collection()
            post.assert_called_once()
            self.assertEqual(post.call_args.args[0], "https://api.firecrawl.dev/v2/search")
            self.assertEqual(post.call_args.kwargs["json"]["limit"], 5)
            self.assertEqual(post.call_args.kwargs["json"]["country"], "cn")
            self.assertIn("industrial lubricants", post.call_args.kwargs["json"]["query"])
            self.assertIn("manufacturers, importers", post.call_args.kwargs["json"]["query"])
            self.assertIn("buyers importers distributors factories procurement", post.call_args.kwargs["json"]["query"])
            with leads_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["email"], "sales@example-manufacturer.cn")
            self.assertEqual(rows[0]["country"], "China")
            self.assertEqual(rows[0]["verification_status"], "search_result_only_needs_review")


    def test_product_web_collection_rejects_ai_classified_suppliers(self):
        import csv
        import tempfile

        class Response:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {"success": True, "data": {"web": [{
                    "url": "https://supplier-example.cn/about",
                    "title": "Industrial Lubricants Manufacturer",
                    "description": "Manufacturer and exporter of industrial lubricants in China.",
                }]}}

        with tempfile.TemporaryDirectory() as tmp:
            leads_path = Path(tmp) / "product_leads.csv"
            with (
                patch.object(social, "FIRECRAWL_API_KEY", "test-firecrawl-key"),
                patch.object(social, "COMPOSIO_API_KEY", ""),
                patch.object(social, "PRODUCT_LEADS", leads_path),
                patch.object(social, "PRODUCT_NAME", "industrial lubricants"),
                patch.object(social, "TARGET_MARKET", "China"),
                patch.object(social, "BUYER_TYPES", "importers, industrial end-users"),
                patch.object(social, "MAX_ITEMS", 3),
                patch.object(social, "GITHUB_RUN_NUMBER", 0),
                patch.object(social, "FIRECRAWL_SEARCHES_THIS_RUN", 0),
                patch.object(social, "qualify_product_lead_with_ai", return_value={
                    "ai_customer_fit": "supplier_only",
                    "ai_confidence": "92",
                    "ai_recommended_buyer_type": "",
                    "ai_reason": "This is a producer, not a target buyer.",
                    "ai_review_status": "ai_screened_review_before_outreach",
                }),
                patch.object(social.session, "post", return_value=Response()),
            ):
                social.run_product_web_collection()
            with leads_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [])


    def test_product_web_collection_cleans_stale_supplier_records(self):
        import csv
        import tempfile

        class Response:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {"success": True, "data": {"web": []}}

        with tempfile.TemporaryDirectory() as tmp:
            leads_path = Path(tmp) / "product_leads.csv"
            with leads_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=social.PRODUCT_LEAD_FIELDS)
                writer.writeheader()
                writer.writerow({
                    "company_name": "Old supplier",
                    "website": "https://old-supplier.example",
                    "country": "China",
                    "product_interest": "industrial lubricants",
                    "ai_customer_fit": "supplier_only",
                })
            with (
                patch.object(social, "FIRECRAWL_API_KEY", "test-firecrawl-key"),
                patch.object(social, "COMPOSIO_API_KEY", ""),
                patch.object(social, "PRODUCT_LEADS", leads_path),
                patch.object(social, "PRODUCT_NAME", "industrial lubricants"),
                patch.object(social, "TARGET_MARKET", "China"),
                patch.object(social, "BUYER_TYPES", "importers, industrial end-users"),
                patch.object(social, "MAX_ITEMS", 3),
                patch.object(social, "GITHUB_RUN_NUMBER", 0),
                patch.object(social, "FIRECRAWL_SEARCHES_THIS_RUN", 0),
                patch.object(social.session, "post", return_value=Response()),
            ):
                social.run_product_web_collection()
            with leads_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [])


    def test_gemini_classifies_product_lead_without_sending_outreach(self):
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key", "GEMINI_MODEL": "test-model"}),
            patch.object(gemini_utils, "generate_text", return_value='{"fit":"likely_customer","confidence":91,"recommended_buyer_type":"industrial end-user","reason":"The evidence identifies an industrial manufacturer with a stated need for the target product.","needs_manual_review":false}') as generate,
        ):
            result = social.qualify_product_lead_with_ai({
                "company_name": "Example Factory",
                "website": "https://example.com",
                "country": "Turkey",
                "buyer_type": "importers, industrial end-users",
                "product_interest": "titanium dioxide",
                "source_url": "https://example.com/about",
                "evidence": "Industrial manufacturer using titanium dioxide in production",
            })
        self.assertEqual(result["ai_customer_fit"], "likely_customer")
        self.assertEqual(result["ai_confidence"], "91")
        self.assertEqual(result["ai_review_status"], "ai_screened_review_before_outreach")
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
