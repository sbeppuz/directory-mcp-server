"""Put three real questions to the server through the official MCP client and print what comes back.

    python demo.py

It starts server.py as a subprocess over stdio, the same way an AI assistant would.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent
SHOWN = ("name", "city", "holes", "website", "distance_km")

CALLS = [
    ("How many courses are there in each region?",
     "count_by", {"column": "region"}),
    ("Which 18-hole courses with a website are within 15 km of Edinburgh Castle?",
     "records_near", {"latitude": 55.9486, "longitude": -3.1999, "radius_km": 15,
                      "where": {"holes": "18"}, "has": ["website"], "limit": 3}),
    ("What happens when the assistant filters on a column that does not exist?",
     "search_records", {"where": {"country": "UK"}}),
]


def summarise(data: dict) -> dict:
    """Keep the printout short: a few fields per row."""
    if "rows" in data:
        data = {**data, "rows": [{k: row[k] for k in SHOWN if row.get(k) not in (None, "")} for row in data["rows"]]}
    return data


async def main() -> None:
    launch = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")])
    async with Client(launch) as client:
        listed = await client.list_tools()
        print("Tools:", ", ".join(tool.name for tool in listed.tools))
        for question, tool, arguments in CALLS:
            result = await client.call_tool(tool, arguments)
            print(f"\nQ: {question}")
            print(f"   {tool}({json.dumps(arguments)})")
            if result.is_error:
                print("   ERROR:", result.content[0].text)
            else:
                print("   " + json.dumps(summarise(result.structured_content), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
