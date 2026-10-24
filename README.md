## TL;DR

ForAI Search is a tiny, offline, zero-dependency Python CLI that indexes SEAN's [ForAI](https://github.com/SeanShaffer88/ForAI) catalog (`resources.json` / `resources.csv`, ~1,000+ curated developer resources) into a local SQLite FTS5 database and searches it in milliseconds with BM25 ranking — no network, no API keys, no pip installs. Python 3.10+, standard library only.

```bash
git clone https://github.com/SeanShaffer88/ForAI
git clone https://github.com/SeanShaffer88/ForAI-Search  # this project (adjust to your clone path)
cd ForAI-Search
python forai_search.py index ../ForAI/resources.json
python forai_search.py search "rust settings library"
python forai_search.py search "MCP server" --category "AI MCP servers and tool use"
python forai_search.py stats
python forai_search.py categories
```

## Usage

### Index

```bash
python forai_search.py index <resources.json|resources.csv> [--db forai.db]
# Indexed 1192 entries across N categories into forai.db
```

Re-running `index` replaces the database cleanly. The `.db` file is a local snapshot — it is git-ignored and never needs to be committed.

### Search

```bash
python forai_search.py search "<query>" [--category C] [--limit 10] [--db forai.db] [--json]
```

Plain output is a ranked list: `name — url`, category, and a one-line description truncated to 120 characters. `--json` emits the full records (name, url, description, category, tags, language, notes). No matches prints a friendly message and exits 0. User input is sanitized before it reaches FTS5 — quotes, `*`, `NEAR(`, and other FTS operators are treated as literal text, never as syntax, so a malformed query cannot crash the search.

### Stats and categories

```bash
python forai_search.py stats       # total entries, distinct URLs, top 10 categories
python forai_search.py categories  # every category with its entry count
```

## Schema tolerance

The real ForAI `resources.json` is a top-level list of objects with `name`, `url`, `category`, `purpose`, `source`, `checked_on`, `review`, and `http_status`. The loader accepts more than that, so older or hand-edited snapshots still index:

| Field | Accepted keys (first match wins) |
|---|---|
| name | `name`, `title` |
| url | `url`, `link` |
| description | `description`, `summary`, `purpose`, `desc` |
| category | `category` |
| tags | `tags`, `tag`, `keywords` (list, or comma-separated string) |
| language | `language`, `lang` |
| notes | `notes`, `note`, `review` |

Top-level JSON may be a list, or an object containing a list under `resources`, `entries`, `items`, or `data`. Missing fields default to empty strings. CSV input uses the same aliases for its header row.

## Limitations

- **Snapshot search:** the index reflects the file you indexed. Re-run `index` after pulling a newer ForAI snapshot to refresh results.
- **FTS5 required:** SQLite must be built with FTS5, which is standard in the SQLite builds shipped with Python on Windows, macOS, and most Linux distributions. If it is missing, `index` reports that clearly instead of failing obscurely.
- **Simple query model:** terms are OR-matched and BM25-ranked (exact-name hits first). There is no phrase, proximity, or boolean query language by design — that keeps arbitrary input safe.
- Search covers name, description, category, tags, and notes. URLs are stored and displayed but not full-text indexed.

## Development

```bash
python -m unittest discover -s tests -v
```

## License

MIT — see [LICENSE](LICENSE).
