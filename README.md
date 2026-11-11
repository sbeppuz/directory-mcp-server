# Directory MCP Server

Let an AI assistant answer questions from your own data.

This is a [Model Context Protocol](https://modelcontextprotocol.io) server that gives Claude, or any other MCP client, **read-only and audited** access to a CSV file. The assistant can search it, filter it, find what is near a point and count things, without ever being able to change a row or see a column you chose to hide.

The sample dataset is a directory of 3,418 golf courses in the UK and Ireland, built in a [separate project](https://github.com/sbeppuz/golf-course-directory). Point the config file at your own CSV and the server answers questions about that instead.

## What it looks like

Real output from `demo.py`, which drives the server through the official MCP client over stdio, the same way Claude Desktop or Claude Code does.

**Which 18-hole courses with a website are within 15 km of Edinburgh Castle?**

```text
records_near({"latitude": 55.9486, "longitude": -3.1999, "radius_km": 15,
              "where": {"holes": "18"}, "has": ["website"], "limit": 3})

{"total_matches": 20, "returned": 3, "limit_applied": 3, "rows": [
  {"name": "Craigmillar Park Golf Club", "holes": "18", "website": "https://www.craigmillarpark.co.uk", "distance_km": 3.28},
  {"name": "Braid Hills Golf Course", "city": "Edinburgh", "holes": "18", "website": "https://www.edinburghleisure.co.uk/venues/braid-hills-golf-course", "distance_km": 3.78},
  {"name": "Murrayfield Golf Course", "city": "Edinburgh", "holes": "18", "website": "https://www.murrayfieldgolfclub.co.uk", "distance_km": 3.78}]}
```

**How many courses are there in each region?**

```text
count_by({"column": "region"})

{"column": "region", "total_rows": 3418, "distinct_values": 5, "groups": [
  {"value": "England", "count": 2120}, {"value": "Scotland", "count": 591}, {"value": "Ireland", "count": 432},
  {"value": "Wales", "count": 178}, {"value": "Northern Ireland", "count": 97}]}
```

**And when the assistant gets a column name wrong?** It receives an error it can read and correct on the next call:

```text
search_records({"where": {"country": "UK"}})

ERROR: Error executing tool search_records: Unknown column 'country'. Valid columns: name, region, city,
county, postcode, street, website, phone, holes, par, course_type, operator, lat, lon, osm_url.
```

## Tools

| Tool | What it does |
|---|---|
| `describe_dataset` | Column names, row count, how complete each column is, the row limit, three sample rows |
| `search_records` | Free-text search plus exact filters, with sorting and paging |
| `records_near` | Records within a radius of a point, nearest first, with the distance in km |
| `count_by` | Count of records for each value of a column, largest first |
| `get_record` | Every visible field of one record |

## Guardrails

- **Read-only by construction.** The engine has no code path that writes to the data, and every tool is declared read-only to the client.
- **Row cap enforced by the server.** Whatever limit the assistant asks for, it never gets more than `max_rows` per call, and the response says which limit was applied.
- **Hidden columns.** Columns listed in `hidden_columns` are dropped while the file is loaded, so they cannot appear in any answer or error message.
- **No query language.** The assistant sends structured filters that are checked against a schema. There is no SQL or expression to inject into.
- **Errors the assistant can act on.** A rejected request explains what was wrong and what is valid.
- **Audit log.** Every call appends one line to `logs/audit.jsonl`: time, tool, arguments, outcome, rows returned, duration.

## Tested

28 automated tests:

- 16 for the query engine, which uses the Python standard library only.
- 12 for the server, driven by the official MCP client: 11 in memory and one that starts `server.py` as a subprocess and talks to it over stdio.

## Run it

Python 3.11 or newer. On Windows:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python -m unittest discover -s tests
```

```bash
.venv\Scripts\python demo.py
```

On macOS or Linux use `.venv/bin/python` instead.

## Connect it to Claude

Claude Code, from any folder:

```bash
claude mcp add golf-directory -- /full/path/to/.venv/Scripts/python.exe /full/path/to/server.py
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "golf-directory": {
      "command": "/full/path/to/.venv/Scripts/python.exe",
      "args": ["/full/path/to/server.py"]
    }
  }
}
```

Then ask in plain language: "Which golf clubs near Cardiff have a phone number listed?"

## Use your own data

Edit `config.json`, or set the `DIRECTORY_MCP_CONFIG` environment variable to another config file.

| Setting | Meaning |
|---|---|
| `server_name` | Name shown to the client |
| `csv_path` | The CSV to serve, relative to the config file |
| `hidden_columns` | Columns that must never reach the assistant |
| `search_columns` | Columns that free-text search looks at |
| `max_rows` | Hard cap on rows returned per call |
| `audit_log` | Where the audit log is written |

Latitude and longitude columns named `lat` and `lon` enable `records_near`.

## Limits

- The CSV is loaded into memory when the server starts. That suits files up to a few hundred thousand rows; beyond that the same tools should sit on top of a database.
- Filters are exact matches and free text is a word search. There is no fuzzy matching and no numeric range filter yet.
- One dataset per server process.

## Built with

Python and the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 2.3.0. Tested on Python 3.14.

The sample data is © OpenStreetMap contributors, available under the Open Database License.

Built with an AI-assisted workflow (Claude Code) and tested before release.
