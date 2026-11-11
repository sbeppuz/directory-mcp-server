"""Unit tests for the query engine. Standard library only: python -m unittest discover -s tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from directory import Directory, QueryError, haversine_km  # noqa: E402

CSV = """name,region,city,website,holes,secret,lat,lon
Alpha Golf Club,Scotland,Edinburgh,https://alpha.example,18,x1,55.95,-3.19
Bravo Links,Scotland,St Andrews,,9,x2,56.34,-2.80
Charlie Park,England,London,https://charlie.example,18,x3,51.50,-0.12
Delta Pitch and Putt,England,Leeds,,,x4,53.80,-1.55
Echo Golf Club,Wales,Cardiff,https://echo.example,27,x5,51.48,-3.18
"""


class DirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "sample.csv"
        path.write_text(CSV, encoding="utf-8")
        cls.d = Directory(path, hidden_columns=["secret"], search_columns=["name", "city", "region"], max_rows=3)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def names(self, result):
        return [row["name"] for row in result["rows"]]

    def test_hidden_columns_never_load(self):
        self.assertNotIn("secret", self.d.columns)
        self.assertTrue(all("secret" not in row for row in self.d.rows))

    def test_describe_reports_shape_and_coverage(self):
        info = self.d.describe()
        self.assertEqual(info["rows"], 5)
        self.assertTrue(info["has_coordinates"])
        website = next(c for c in info["columns"] if c["name"] == "website")
        self.assertEqual(website["filled_percent"], 60.0)
        region = next(c for c in info["columns"] if c["name"] == "region")
        self.assertEqual(region["values"], ["England", "Scotland", "Wales"])

    def test_text_search_needs_every_word(self):
        self.assertEqual(self.names(self.d.search(text="golf club")), ["Alpha Golf Club", "Echo Golf Club"])
        self.assertEqual(self.names(self.d.search(text="golf scotland")), ["Alpha Golf Club"])
        self.assertEqual(self.d.search(text="nothing here")["total_matches"], 0)

    def test_where_is_exact_and_case_insensitive(self):
        self.assertEqual(self.d.search(where={"region": "scotland"})["total_matches"], 2)
        self.assertEqual(self.d.search(where={"holes": ["9", "27"]})["total_matches"], 2)
        self.assertEqual(self.d.search(where={"city": "Edin"})["total_matches"], 0)

    def test_has_requires_a_value(self):
        self.assertEqual(self.names(self.d.search(has=["website"], where={"region": "England"})), ["Charlie Park"])

    def test_unknown_column_explains_the_valid_ones(self):
        with self.assertRaises(QueryError) as caught:
            self.d.search(where={"country": "UK"})
        self.assertIn("Valid columns", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_limit_is_capped_and_reported(self):
        result = self.d.search(limit=100)
        self.assertEqual((result["total_matches"], result["returned"], result["limit_applied"]), (5, 3, 3))

    def test_offset_pages_through_results(self):
        self.assertEqual(self.names(self.d.search(sort_by="name", limit=2, offset=2)), ["Charlie Park", "Delta Pitch and Putt"])

    def test_numeric_sort_puts_empty_values_last(self):
        result = self.d.search(sort_by="holes", descending=True, limit=3)
        self.assertEqual(self.names(result)[0], "Echo Golf Club")
        ascending = Directory._sorted(self.d.rows, "holes", False)
        self.assertEqual(ascending[0]["holes"], "9")
        self.assertEqual(ascending[-1]["holes"], "")

    def test_bad_limit_and_offset_are_rejected(self):
        for kwargs in ({"limit": 0}, {"offset": -1}):
            with self.assertRaises(QueryError):
                self.d.search(**kwargs)

    def test_nearby_sorts_by_distance_and_respects_radius(self):
        result = self.d.nearby(55.95, -3.19, radius_km=80)
        self.assertEqual(self.names(result), ["Alpha Golf Club", "Bravo Links"])
        self.assertEqual(result["rows"][0]["distance_km"], 0.0)
        self.assertEqual(self.d.nearby(55.95, -3.19, radius_km=10)["total_matches"], 1)

    def test_nearby_rejects_impossible_input(self):
        for args in ((95, 0, 10), (0, 200, 10), (50, 0, 0)):
            with self.assertRaises(QueryError):
                self.d.nearby(*args)

    def test_count_by_groups_and_labels_empty(self):
        result = self.d.count_by("holes")
        self.assertEqual(result["groups"][0], {"value": "18", "count": 2})
        self.assertIn({"value": "(empty)", "count": 1}, result["groups"])
        self.assertEqual(self.d.count_by("region", has=["website"])["total_rows"], 3)

    def test_get_returns_one_record_or_explains(self):
        self.assertEqual(self.d.get("2")["name"], "Bravo Links")
        for bad in ("0", "6", "abc"):
            with self.assertRaises(QueryError):
                self.d.get(bad)

    def test_csv_with_its_own_id_column_is_refused(self):
        path = Path(self.tmp.name) / "with_id.csv"
        path.write_text("id,name\n1,x\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            Directory(path)

    def test_haversine_london_to_paris(self):
        self.assertAlmostEqual(haversine_km(51.5074, -0.1278, 48.8566, 2.3522), 343.5, delta=2)


if __name__ == "__main__":
    unittest.main()
