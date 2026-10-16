import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import lead_collector


class LeadFilterTests(unittest.TestCase):
    def test_china_search_context_allows_company_domain_without_china_in_snippet(self):
        rejected = lead_collector.looks_like_reject(
            "Jiangsu Industrial Lubricants Manufacturing Co.",
            "https://example.com",
            "Industrial lubricants manufacturer with blending and production operations.",
            "industrial lubricant manufacturer China Jiangsu",
        )
        self.assertFalse(rejected)

    def test_editorial_article_is_rejected(self):
        rejected = lead_collector.looks_like_reject(
            "Top industrial lubricant suppliers in China: industry guide",
            "https://example.com/industry-guide",
            "A guide to industrial lubricant companies.",
            "industrial lubricant suppliers China",
        )
        self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main()
