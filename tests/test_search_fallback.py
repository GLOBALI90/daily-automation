import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import run_collector_resilient as resilient


class SearchFallbackTests(unittest.TestCase):
    def test_html_fallback_preserves_result_title_and_snippet(self):
        page = """
        <article class="result">
          <a class="url_header result_header" href="https://example.cn/company">
            <h3>Example Chemical Manufacturer</h3>
          </a>
          <p class="content">Industrial chemical producer based in China.</p>
        </article>
        """
        results = resilient._html_results(page, 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.cn/company")
        self.assertIn("Example Chemical Manufacturer", results[0]["title"])
        self.assertIn("industrial chemical producer", results[0]["content"].lower())


if __name__ == "__main__":
    unittest.main()
