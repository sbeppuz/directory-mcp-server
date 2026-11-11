"""Read-only query engine over one CSV file.

Standard library only: this module knows nothing about MCP, so it can be
tested and reused on its own. Every public method validates its input and
raises QueryError with a message written for the caller to act on.
"""
from __future__ import annotations

import csv
import math
from collections import Counter
from pathlib import Path


class QueryError(ValueError):
    """The request was understood but cannot be answered as asked."""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


class Directory:
    """An in-memory table loaded once from a CSV file. Nothing here can write."""

    def __init__(
        self,
        csv_path: str | Path,
        *,
        hidden_columns: tuple[str, ...] | list[str] = (),
        search_columns: list[str] | None = None,
        lat_column: str = "lat",
        lon_column: str = "lon",
        max_rows: int = 50,
    ) -> None:
        path = Path(csv_path)
        hidden = set(hidden_columns)
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            source_columns = list(reader.fieldnames or [])
            if "id" in source_columns:
                raise ValueError('The CSV already has an "id" column. Rename it: "id" is the row number added by the server.')
            self.columns = [c for c in source_columns if c not in hidden]
            self.rows = [
                {"id": str(number), **{c: (raw.get(c) or "").strip() for c in self.columns}}
                for number, raw in enumerate(reader, start=1)
            ]
        self.name = path.stem
        self.max_rows = max_rows
        self.search_columns = [c for c in (search_columns or self.columns) if c in self.columns]
        self.lat_column, self.lon_column = lat_column, lon_column
        self.has_coordinates = lat_column in self.columns and lon_column in self.columns

    # ------------------------------------------------------------ validation

    def _check_column(self, column: str) -> None:
        if column not in self.columns:
            raise QueryError(f"Unknown column {column!r}. Valid columns: {', '.join(self.columns)}.")

    def _limit(self, limit: int) -> int:
        if limit < 1:
            raise QueryError("limit must be 1 or more.")
        return min(limit, self.max_rows)

    def _matching(self, where: dict | None = None, has: list[str] | None = None, text: str | None = None) -> list[dict]:
        where, has = where or {}, has or []
        for column in [*where, *has]:
            self._check_column(column)
        wanted = {
            column: {str(v).strip().lower() for v in (values if isinstance(values, list) else [values])}
            for column, values in where.items()
        }
        words = (text or "").lower().split()
        rows = []
        for row in self.rows:
            if any(row[column].lower() not in allowed for column, allowed in wanted.items()):
                continue
            if any(not row[column] for column in has):
                continue
            if words and not all(any(word in row[c].lower() for c in self.search_columns) for word in words):
                continue
            rows.append(row)
        return rows

    @staticmethod
    def _sorted(rows: list[dict], column: str, descending: bool) -> list[dict]:
        """Sort by a column, numerically when every value is a number. Empty values go last."""
        filled = [r for r in rows if r[column]]
        empty = [r for r in rows if not r[column]]
        try:
            filled = sorted(filled, key=lambda r: float(r[column]), reverse=descending)
        except ValueError:
            filled = sorted(filled, key=lambda r: r[column].lower(), reverse=descending)
        return filled + empty

    # --------------------------------------------------------------- queries

    def describe(self) -> dict:
        total = len(self.rows)
        columns = []
        for column in self.columns:
            values = [r[column] for r in self.rows if r[column]]
            distinct = set(values)
            info: dict = {
                "name": column,
                "filled_percent": round(100 * len(values) / total, 1) if total else 0.0,
                "distinct_values": len(distinct),
            }
            if 0 < len(distinct) <= 12:
                info["values"] = sorted(distinct)
            columns.append(info)
        return {
            "dataset": self.name,
            "rows": total,
            "columns": columns,
            "has_coordinates": self.has_coordinates,
            "text_search_columns": self.search_columns,
            "max_rows_per_call": self.max_rows,
            "sample": self.rows[:3],
        }

    def search(
        self,
        text: str | None = None,
        where: dict | None = None,
        has: list[str] | None = None,
        sort_by: str | None = None,
        descending: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> dict:
        if offset < 0:
            raise QueryError("offset must be 0 or more.")
        limit_applied = self._limit(limit)
        rows = self._matching(where, has, text)
        if sort_by:
            self._check_column(sort_by)
            rows = self._sorted(rows, sort_by, descending)
        page = rows[offset:offset + limit_applied]
        return {
            "total_matches": len(rows),
            "returned": len(page),
            "offset": offset,
            "limit_applied": limit_applied,
            "rows": page,
        }

    def nearby(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 25,
        where: dict | None = None,
        has: list[str] | None = None,
        limit: int = 10,
    ) -> dict:
        if not self.has_coordinates:
            raise QueryError("This dataset has no latitude and longitude columns.")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise QueryError("latitude must be between -90 and 90, longitude between -180 and 180.")
        if radius_km <= 0:
            raise QueryError("radius_km must be greater than 0.")
        limit_applied = self._limit(limit)
        hits = []
        for row in self._matching(where, has):
            try:
                distance = haversine_km(latitude, longitude, float(row[self.lat_column]), float(row[self.lon_column]))
            except ValueError:
                continue  # a row without usable coordinates cannot be near anything
            if distance <= radius_km:
                hits.append({**row, "distance_km": round(distance, 2)})
        hits.sort(key=lambda r: r["distance_km"])
        page = hits[:limit_applied]
        return {"total_matches": len(hits), "returned": len(page), "limit_applied": limit_applied, "rows": page}

    def count_by(self, column: str, where: dict | None = None, has: list[str] | None = None, top: int = 20) -> dict:
        self._check_column(column)
        if not 1 <= top <= 100:
            raise QueryError("top must be between 1 and 100.")
        rows = self._matching(where, has)
        counts = Counter(row[column] or "(empty)" for row in rows)
        return {
            "column": column,
            "total_rows": len(rows),
            "distinct_values": len(counts),
            "groups": [{"value": value, "count": count} for value, count in counts.most_common(top)],
        }

    def get(self, record_id: str) -> dict:
        try:
            index = int(str(record_id).strip()) - 1
        except ValueError:
            index = -1
        if not 0 <= index < len(self.rows):
            raise QueryError(f"No record with id {record_id!r}. Ids run from 1 to {len(self.rows)}.")
        return self.rows[index]
