#!/usr/bin/env python3
"""ForAI Search — local, offline search over SEAN's ForAI catalog.

Stdlib only (Python 3.10+). Builds a SQLite + FTS5 index from a
ForAI ``resources.json`` or ``resources.csv`` snapshot and searches it
with BM25 ranking.

Real ForAI schema (inspected 2026-10-24):
    top-level JSON list of objects with keys
    name, url, category, purpose, source, checked_on, review, http_status
    (``purpose`` is the description field in the real catalog)

The loader is deliberately schema-tolerant — see FIELD_ALIASES below.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# First matching alias wins. Keys are compared case-insensitively.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "title"),
    "url": ("url", "link"),
    "description": ("description", "summary", "purpose", "desc"),
    "category": ("category",),
    "tags": ("tags", "tag", "keywords"),
    "language": ("language", "lang"),
    "notes": ("notes", "note", "review"),
}
FIELDS = tuple(FIELD_ALIASES.keys())
CONTAINER_KEYS = ("resources", "entries", "items", "data")


def _pick(entry: dict, field: str) -> str:
    lowered = {str(k).lower(): v for k, v in entry.items()}
    for alias in FIELD_ALIASES[field]:
        if alias in lowered and lowered[alias] is not None:
            value = lowered[alias]
            if field == "tags" and isinstance(value, (list, tuple)):
                return ", ".join(str(v).strip() for v in value if str(v).strip())
            if isinstance(value, (list, tuple)):
                return ", ".join(str(v) for v in value)
            return str(value).strip()
    return ""


def normalize_entry(entry: dict) -> dict[str, str]:
    if not isinstance(entry, dict):
        return {f: "" for f in FIELDS}
    return {f: _pick(entry, f) for f in FIELDS}


def load_json(path: str | Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        raw: list = []
        for key in CONTAINER_KEYS:
            if isinstance(data.get(key), list):
                raw = data[key]
                break
        else:
            raw = [data] if data else []
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError(f"Unsupported JSON top-level type: {type(data).__name__}")
    return [normalize_entry(e) for e in raw if isinstance(e, dict)]


def load_csv(path: str | Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []
        return [normalize_entry(row) for row in reader if row]


def load_entries(path: str | Path) -> list[dict[str, str]]:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".json":
        return load_json(path)
    # Sniff content when the extension is unknown.
    text = Path(path).read_text(encoding="utf-8-sig").lstrip()
    return load_json(path) if text[:1] in "[{" else load_csv(path)


def build_index(source: str | Path, db_path: str | Path) -> tuple[int, int]:
    """Build (or cleanly rebuild) the SQLite/FTS5 index. Returns (entries, categories)."""
    entries = load_entries(source)
    db = Path(db_path)
    if db.exists():
        db.unlink()
    if str(db.parent) not in ("", "."):
        db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE resources ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
            "url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
            "category TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', "
            "language TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '')"
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE resources_fts USING fts5("
                "name, description, category, tags, notes, "
                "content='resources', content_rowid='id')"
            )
        except sqlite3.OperationalError as exc:
            raise SystemExit(
                "SQLite FTS5 is not available in this Python/SQLite build: " + str(exc)
            ) from exc
        conn.executemany(
            "INSERT INTO resources (name, url, description, category, tags, language, notes)"
            " VALUES (:name, :url, :description, :category, :tags, :language, :notes)",
            entries,
        )
        conn.execute("INSERT INTO resources_fts(resources_fts) VALUES('rebuild')")
        conn.commit()
        cat_count = conn.execute(
            "SELECT COUNT(DISTINCT category) FROM resources WHERE category <> ''"
        ).fetchone()[0]
    finally:
        conn.close()
    return len(entries), int(cat_count)


def sanitize_fts_query(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Every whitespace-separated term is double-quoted (inner quotes doubled),
    so FTS operators ("*", NEAR(...), bare quotes, AND/OR/NOT, ^, etc.)
    in user input are treated as literal text, never as syntax.
    Returns "" when there is no searchable term.
    """
    terms = []
    for raw in re.split(r"\s+", (query or "").strip()):
        term = raw.replace('"', "").strip()
        if term:
            terms.append(term)
    quoted = ['"' + t + '"' for t in terms]
    return " OR ".join(quoted)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise SystemExit(f"Database not found: {db_path} — run `index` first.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def search_entries(
    db_path: str | Path, query: str, category: str | None = None, limit: int = 10
) -> list[dict[str, str]]:
    match = sanitize_fts_query(query)
    if not match:
        return []
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT r.name, r.url, r.description, r.category, r.tags, r.language, r.notes, "
            "bm25(resources_fts) AS rank "
            "FROM resources_fts JOIN resources r ON r.id = resources_fts.rowid "
            "WHERE resources_fts MATCH ?"
        )
        params: list = [match]
        if category:
            sql += " AND r.category = ? COLLATE NOCASE"
            params.append(category)
        # Exact-name hits first, then BM25 relevance.
        sql += " ORDER BY (CASE WHEN lower(r.name) = lower(?) THEN 0 ELSE 1 END), rank LIMIT ?"
        params += [query.strip(), int(limit)]
        rows = conn.execute(sql, params).fetchall()
        return [{k: row[k] for k in FIELDS} for row in rows]
    finally:
        conn.close()


def get_stats(db_path: str | Path) -> dict:
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        distinct_urls = conn.execute(
            "SELECT COUNT(DISTINCT url) FROM resources WHERE url <> ''"
        ).fetchone()[0]
        top = conn.execute(
            "SELECT category, COUNT(*) AS n FROM resources WHERE category <> '' "
            "GROUP BY category ORDER BY n DESC, category ASC LIMIT 10"
        ).fetchall()
        return {
            "total": int(total),
            "distinct_urls": int(distinct_urls),
            "top_categories": [(r[0], int(r[1])) for r in top],
        }
    finally:
        conn.close()


def get_categories(db_path: str | Path) -> list[tuple[str, int]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM resources WHERE category <> '' "
            "GROUP BY category ORDER BY n DESC, category ASC"
        ).fetchall()
        return [(r[0], int(r[1])) for r in rows]
    finally:
        conn.close()


def _truncate(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forai_search", description="Local offline search over the ForAI catalog."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Build/rebuild the SQLite FTS5 index")
    p_index.add_argument("source", help="Path to resources.json or resources.csv")
    p_index.add_argument("--db", default="forai.db")

    p_search = sub.add_parser("search", help="Search the index (BM25 ranked)")
    p_search.add_argument("query")
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--db", default="forai.db")
    p_search.add_argument("--json", action="store_true", help="Emit full records as JSON")

    p_stats = sub.add_parser("stats", help="Totals and top categories")
    p_stats.add_argument("--db", default="forai.db")

    p_cats = sub.add_parser("categories", help="All categories with counts")
    p_cats.add_argument("--db", default="forai.db")

    args = parser.parse_args(argv)

    if args.command == "index":
        if not Path(args.source).exists():
            print(f"Source not found: {args.source}", file=sys.stderr)
            return 1
        count, cats = build_index(args.source, args.db)
        print(f"Indexed {count} entries across {cats} categories into {args.db}")
        return 0

    if args.command == "search":
        results = search_entries(args.db, args.query, category=args.category, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
            return 0
        if not results:
            print(f'No results for "{args.query}". Try fewer or different terms.')
            return 0
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['name']} — {r['url']}")
            if r["category"]:
                print(f"   [{r['category']}]")
            if r["description"]:
                print(f"   {_truncate(r['description'])}")
        return 0

    if args.command == "stats":
        s = get_stats(args.db)
        print(f"Total entries: {s['total']}")
        print(f"Distinct URLs: {s['distinct_urls']}")
        print("Top categories:")
        for name, n in s["top_categories"]:
            print(f"  {n:>5}  {name}")
        return 0

    if args.command == "categories":
        cats = get_categories(args.db)
        if not cats:
            print("No categories found.")
            return 0
        for name, n in cats:
            print(f"{n:>5}  {name}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
