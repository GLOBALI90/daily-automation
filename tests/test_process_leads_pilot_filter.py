import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import process_leads


class ProcessLeadsPilotFilterTests(unittest.TestCase):
    def test_accepts_evidenced_chinese_lubricant_manufacturer(self):
        row = {
            "country": "China",
            "company_name": "Jiangsu Industrial Lubricants Factory",
            "website": "https://example.cn",
            "social_url": "https://www.linkedin.com/company/jiangsu-lubricants",
            "social_bio": "Industrial lubricant manufacturer and production factory in Jiangsu, China",
            "industry": "industrial lubricants",
            "product_interest": "industrial lubricants",
            "buyer_type": "industrial-lubricant manufacturer / producer",
        }
        self.assertTrue(process_leads.is_pilot_target(row))

    def test_rejects_historical_auto_parts_lead(self):
        row = {
            "country": "China",
            "company_name": "Ningbo Motor Industrial Co",
            "website": "https://example.cn",
            "social_url": "https://www.linkedin.com/company/ningbo-motor",
            "social_bio": "Automotive parts, steering and suspension components factory",
            "industry": "automotive parts",
            "product_interest": "automotive parts",
            "buyer_type": "manufacturer",
        }
        self.assertFalse(process_leads.is_pilot_target(row))

    def test_rejects_lubricant_company_without_china_evidence(self):
        row = {
            "country": "China",
            "company_name": "Global Lubricants Manufacturer",
            "website": "https://example.com",
            "social_url": "https://www.linkedin.com/company/global-lubricants",
            "social_bio": "Industrial lubricants manufacturer and production factory",
            "industry": "industrial lubricants",
            "product_interest": "industrial lubricants",
            "buyer_type": "manufacturer",
        }
        self.assertFalse(process_leads.is_pilot_target(row))


if __name__ == "__main__":
    unittest.main()
