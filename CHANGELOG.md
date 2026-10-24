# Changelog

## 0.1.0 — 2026-10-24

- Initial release: `index`, `search`, `stats`, and `categories` commands.
- SQLite + FTS5 index with BM25 ranking and exact-name boosting; stdlib only.
- Schema-tolerant loader for JSON (list or container object) and CSV, with field aliases including the real ForAI `purpose` description field.
- FTS query sanitization: user input is quoted term-by-term and cannot inject FTS5 syntax.
