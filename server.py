"""MCP server that lets an AI assistant query a CSV directory. Read-only.

    python server.py      run over stdio, which is how Claude Desktop and Claude Code launch it

The dataset, the columns to hide and the row limit come from config.json.
Set DIRECTORY_MCP_CONFIG to use another config file.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from directory import Directory, QueryError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("DIRECTORY_MCP_CONFIG", ROOT / "config.json"))
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _from_config_dir(path: str) -> Path:
    """Paths in the config file are relative to the config file itself."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else CONFIG_PATH.parent / candidate


DIRECTORY = Directory(
    _from_config_dir(CONFIG["csv_path"]),
    hidden_columns=CONFIG.get("hidden_columns", []),
    search_columns=CONFIG.get("search_columns"),
    max_rows=CONFIG.get("max_rows", 50),
)
AUDIT_LOG = _from_config_dir(CONFIG.get("audit_log", "logs/audit.jsonl"))

# Every tool only reads a fixed local file: say so, so clients can skip confirmation prompts.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    CONFIG.get("server_name", "Directory"),
    version="1.0.0",
    instructions=(
        "Read-only access to one dataset. Call describe_dataset first to learn the column names, "
        "then use search_records, records_near, count_by or get_record. Filters need exact column names."
    ),
)

Where = Annotated[
    dict[str, str | list[str]] | None,
    Field(description='Exact-match filters by column name, case-insensitive. A list means "any of these". '
                      'Example: {"region": "Scotland", "holes": ["9", "18"]}.'),
]
Has = Annotated[
    list[str] | None,
    Field(description='Columns that must not be empty. Example: ["website", "phone"].'),
]
Limit = Annotated[
    int,
    Field(ge=1, le=500, description="Maximum rows to return. The server caps this at its configured limit "
                                    "and reports the limit it applied."),
]


def audited(tool: str, arguments: dict[str, Any], query: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one query and append one line to the audit log, whatever the outcome.

    A request the engine rejects becomes a ToolError, so the model reads the
    reason and can correct its next call.
    """
    started = time.perf_counter()
    outcome, returned = "ok", None
    try:
        result = query()
        returned = result.get("returned")
        return result
    except QueryError as exc:
        outcome = f"rejected: {exc}"
        raise ToolError(str(exc)) from exc
    except Exception:
        outcome = "failed"
        raise
    finally:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "arguments": {key: value for key, value in arguments.items() if value is not None},
            "outcome": outcome,
            "returned": returned,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


@mcp.tool(title="Describe the dataset", annotations=READ_ONLY)
def describe_dataset() -> dict[str, Any]:
    """Column names, row count, how complete each column is, the row limit and three sample rows. Call this first."""
    return audited("describe_dataset", {}, DIRECTORY.describe)


@mcp.tool(title="Search records", annotations=READ_ONLY)
def search_records(
    text: Annotated[str | None, Field(description="Words that must all appear in the searchable text columns. Case-insensitive.")] = None,
    where: Where = None,
    has: Has = None,
    sort_by: Annotated[str | None, Field(description="Column to sort by. Numbers sort numerically; empty values go last.")] = None,
    descending: bool = False,
    limit: Limit = 10,
    offset: Annotated[int, Field(ge=0, description="Rows to skip, for paging through results.")] = 0,
) -> dict[str, Any]:
    """Find records by free text and exact filters. Returns the total number of matches and one page of rows."""
    arguments = {"text": text, "where": where, "has": has, "sort_by": sort_by,
                 "descending": descending, "limit": limit, "offset": offset}
    return audited("search_records", arguments, lambda: DIRECTORY.search(**arguments))


@mcp.tool(title="Find records near a point", annotations=READ_ONLY)
def records_near(
    latitude: Annotated[float, Field(ge=-90, le=90, description="Latitude of the centre point, in decimal degrees.")],
    longitude: Annotated[float, Field(ge=-180, le=180, description="Longitude of the centre point, in decimal degrees.")],
    radius_km: Annotated[float, Field(gt=0, le=1000, description="Search radius in kilometres.")] = 25,
    where: Where = None,
    has: Has = None,
    limit: Limit = 10,
) -> dict[str, Any]:
    """Records within a radius of a point, nearest first, each with its distance in kilometres."""
    arguments = {"latitude": latitude, "longitude": longitude, "radius_km": radius_km,
                 "where": where, "has": has, "limit": limit}
    return audited("records_near", arguments, lambda: DIRECTORY.nearby(**arguments))


@mcp.tool(title="Count records by column", annotations=READ_ONLY)
def count_by(
    column: Annotated[str, Field(description="Column to group by.")],
    where: Where = None,
    has: Has = None,
    top: Annotated[int, Field(ge=1, le=100, description="How many of the largest groups to return.")] = 20,
) -> dict[str, Any]:
    """Count the records for each value of a column, largest group first. Useful for totals and breakdowns."""
    arguments = {"column": column, "where": where, "has": has, "top": top}
    return audited("count_by", arguments, lambda: DIRECTORY.count_by(**arguments))


@mcp.tool(title="Get one record", annotations=READ_ONLY)
def get_record(
    record_id: Annotated[str, Field(description='The "id" of a row returned by another tool.')],
) -> dict[str, Any]:
    """Return every visible field of one record."""
    return audited("get_record", {"record_id": record_id}, lambda: DIRECTORY.get(record_id))


if __name__ == "__main__":
    mcp.run()
