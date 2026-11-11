"""Integration tests: the real MCP server, driven by the official MCP client.

Most tests connect in memory. The last class starts server.py as a subprocess
and talks to it over stdio, exactly as Claude Desktop or Claude Code would.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import Client, StdioServerParameters  # noqa: E402

import server  # noqa: E402

EXPECTED_TOOLS = {"describe_dataset", "search_records", "records_near", "count_by", "get_record"}


def text_of(result) -> str:
    return " ".join(block.text for block in result.content if hasattr(block, "text"))


class InMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool: str, arguments: dict | None = None):
        async with Client(server.mcp) as client:
            return await client.call_tool(tool, arguments or {})

    async def test_five_tools_are_listed_and_marked_read_only(self):
        async with Client(server.mcp) as client:
            listed = await client.list_tools()
        self.assertEqual({tool.name for tool in listed.tools}, EXPECTED_TOOLS)
        for tool in listed.tools:
            self.assertTrue(tool.annotations.read_only_hint, tool.name)
            self.assertFalse(tool.annotations.destructive_hint, tool.name)
            self.assertTrue(tool.description, tool.name)
            self.assertEqual(tool.input_schema["type"], "object")

    async def test_describe_dataset_reports_the_real_file(self):
        info = (await self.call("describe_dataset")).structured_content
        self.assertEqual(info["rows"], 3418)
        self.assertTrue(info["has_coordinates"])
        self.assertEqual(info["max_rows_per_call"], 50)
        self.assertIn("website", [column["name"] for column in info["columns"]])

    async def test_search_applies_filters(self):
        result = await self.call("search_records", {"where": {"region": "Scotland"}, "has": ["website"], "limit": 5})
        data = result.structured_content
        self.assertFalse(result.is_error)
        self.assertEqual(data["returned"], 5)
        self.assertGreater(data["total_matches"], 100)
        for row in data["rows"]:
            self.assertEqual(row["region"], "Scotland")
            self.assertTrue(row["website"])

    async def test_text_search_needs_every_word(self):
        data = (await self.call("search_records", {"text": "st andrews", "where": {"region": "Scotland"}, "limit": 50})).structured_content
        self.assertGreaterEqual(data["total_matches"], 5)
        for row in data["rows"]:
            searchable = " ".join(row[c] for c in ("name", "city", "county", "postcode", "operator", "region")).lower()
            self.assertIn("andrews", searchable)

    async def test_records_near_come_back_nearest_first(self):
        data = (await self.call("records_near", {"latitude": 55.9533, "longitude": -3.1883, "radius_km": 10})).structured_content
        distances = [row["distance_km"] for row in data["rows"]]
        self.assertGreaterEqual(data["total_matches"], 5)
        self.assertEqual(distances, sorted(distances))
        self.assertLessEqual(max(distances), 10)

    async def test_count_by_region_matches_the_dataset(self):
        data = (await self.call("count_by", {"column": "region"})).structured_content
        self.assertEqual(data["total_rows"], 3418)
        self.assertIn({"value": "England", "count": 2120}, data["groups"])
        self.assertIn({"value": "Scotland", "count": 591}, data["groups"])

    async def test_get_record_returns_one_row_or_a_readable_error(self):
        row = (await self.call("get_record", {"record_id": "1"})).structured_content
        self.assertEqual(row["id"], "1")
        self.assertTrue(row["name"])
        missing = await self.call("get_record", {"record_id": "999999"})
        self.assertTrue(missing.is_error)
        self.assertIn("Ids run from 1 to 3418", text_of(missing))

    async def test_unknown_column_is_an_error_the_model_can_fix(self):
        result = await self.call("search_records", {"where": {"country": "UK"}})
        self.assertTrue(result.is_error)
        self.assertIsNone(result.structured_content)
        self.assertIn("Unknown column 'country'", text_of(result))
        self.assertIn("Valid columns", text_of(result))

    async def test_arguments_outside_the_schema_are_rejected(self):
        for arguments in ({"limit": 0}, {"offset": -1}, {"limit": "many"}):
            result = await self.call("search_records", arguments)
            self.assertTrue(result.is_error, arguments)

    async def test_row_limit_is_enforced_by_the_server(self):
        data = (await self.call("search_records", {"limit": 500})).structured_content
        self.assertEqual((data["returned"], data["limit_applied"]), (50, 50))
        self.assertEqual(data["total_matches"], 3418)

    async def test_every_call_leaves_an_audit_line(self):
        before = len(server.AUDIT_LOG.read_text(encoding="utf-8").splitlines()) if server.AUDIT_LOG.exists() else 0
        await self.call("count_by", {"column": "holes", "top": 3})
        await self.call("search_records", {"where": {"nope": "x"}})
        lines = server.AUDIT_LOG.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), before + 2)
        accepted, rejected = (json.loads(line) for line in lines[-2:])
        self.assertEqual((accepted["tool"], accepted["outcome"]), ("count_by", "ok"))
        self.assertEqual(accepted["arguments"], {"column": "holes", "top": 3})
        self.assertTrue(rejected["outcome"].startswith("rejected: Unknown column"))


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_works_as_a_subprocess_over_stdio(self):
        launch = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")])
        async with Client(launch) as client:
            listed = await client.list_tools()
            result = await client.call_tool("count_by", {"column": "region"})
        self.assertEqual({tool.name for tool in listed.tools}, EXPECTED_TOOLS)
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["total_rows"], 3418)


if __name__ == "__main__":
    unittest.main()
