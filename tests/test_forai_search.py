import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forai_search as fs  # noqa: E402

JSON_ENTRIES = [
    {"name": "Rust", "url": "https://www.rust-lang.org/",
     "description": "Systems programming language focused on safety and speed.",
     "category": "Languages", "tags": ["rust", "systems"], "language": "Rust",
     "notes": "Official site"},
    {"name": "Rust Cookbook", "url": "https://rust-lang-nursery.github.io/rust-cookbook/",
     "description": "Rust Rust Rust recipes and examples for common Rust tasks.",
     "category": "Libraries", "tags": "rust, examples", "language": "Rust", "notes": ""},
    {"name": "Python", "url": "https://www.python.org/",
     "description": "General purpose programming language.",
     "category": "Languages", "tags": ["python"], "language": "Python", "notes": ""},
    {"name": "SQLite", "url": "https://sqlite.org/",
     "description": "Embedded SQL database engine with FTS5 full text search.",
     "category": "Databases", "tags": ["sql", "embedded"], "language": "C", "notes": ""},
    {"name": "SQLite Documentation Mirror", "url": "https://sqlite.org/",
     "description": "Mirror entry sharing the SQLite URL for distinct-count tests.",
     "category": "Databases", "tags": ["sql"], "language": "C", "notes": "duplicate url"},
    {"name": "WinUI 3 Gallery", "url": "https://github.com/microsoft/WinUI-Gallery",
     "description": "Sample gallery for Windows desktop controls.",
     "category": "Windows", "tags": ["winui", "windows"], "language": "C#", "notes": ""},
]


class ForaiSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.json_path = self.dir / "resources.json"
        self.json_path.write_text(json.dumps(JSON_ENTRIES), encoding="utf-8")
        self.db = self.dir / "forai.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = fs.main(argv)
        return code, out.getvalue()

    def test_index_builds_from_json_list(self):
        code, out = self._run(["index", str(self.json_path), "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("Indexed 6 entries", out)
        self.assertIn("4 categories", out)
        self.assertTrue(self.db.exists())

    def test_reindex_replaces_cleanly(self):
        fs.build_index(self.json_path, self.db)
        count, _ = fs.build_index(self.json_path, self.db)
        self.assertEqual(count, 6)
        self.assertEqual(fs.get_stats(self.db)["total"], 6)

    def test_index_from_csv_with_aliases(self):
        csv_path = self.dir / "resources.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["title", "link", "summary", "category", "tags", "language", "notes"])
            for e in JSON_ENTRIES:
                w.writerow([e["name"], e["url"], e["description"], e["category"],
                            ",".join(e["tags"]) if isinstance(e["tags"], list) else e["tags"],
                            e["language"], e["notes"]])
        count, cats = fs.build_index(csv_path, self.db)
        self.assertEqual((count, cats), (6, 4))
        results = fs.search_entries(self.db, "SQLite")
        self.assertTrue(results)
        self.assertEqual(results[0]["url"], "https://sqlite.org/")

    def test_dict_container_and_field_aliases(self):
        alt = self.dir / "alt.json"
        alt.write_text(json.dumps({"resources": [
            {"title": "Aliased Tool", "link": "https://example.com/tool",
             "summary": "Uses title link and summary aliases.", "category": "Tools",
             "tags": "alias", "lang": "Python", "note": "n"}
        ]}), encoding="utf-8")
        entries = fs.load_json(alt)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "Aliased Tool")
        self.assertEqual(entries[0]["url"], "https://example.com/tool")
        self.assertEqual(entries[0]["description"], "Uses title link and summary aliases.")
        self.assertEqual(entries[0]["language"], "Python")

    def test_real_forai_purpose_alias(self):
        entries = [fs.normalize_entry(
            {"name": "X", "url": "https://example.com", "category": "C",
             "purpose": "The real ForAI description field.", "review": "checked"})]
        self.assertEqual(entries[0]["description"], "The real ForAI description field.")
        self.assertEqual(entries[0]["notes"], "checked")

    def test_search_ranks_exact_name_first(self):
        fs.build_index(self.json_path, self.db)
        results = fs.search_entries(self.db, "Rust")
        self.assertTrue(results)
        self.assertEqual(results[0]["name"], "Rust")

    def test_category_filter(self):
        fs.build_index(self.json_path, self.db)
        results = fs.search_entries(self.db, "Rust", category="Languages")
        self.assertEqual([r["name"] for r in results], ["Rust"])
        self.assertEqual(fs.search_entries(self.db, "Rust", category="Databases"), [])

    def test_fts_special_chars_do_not_crash(self):
        fs.build_index(self.json_path, self.db)
        for q in ('"', "*", "NEAR(", 'a " b * NEAR( c)', 'Rust" OR *'):
            results = fs.search_entries(self.db, q)  # must not raise
            self.assertIsInstance(results, list)
        code, out = self._run(["search", 'NEAR( " *', "--db", str(self.db)])
        self.assertEqual(code, 0)

    def test_stats_counts_and_distinct_urls(self):
        fs.build_index(self.json_path, self.db)
        s = fs.get_stats(self.db)
        self.assertEqual(s["total"], 6)
        self.assertEqual(s["distinct_urls"], 5)  # one duplicate URL in the fixture
        top = dict(s["top_categories"])
        self.assertEqual(top["Languages"], 2)
        self.assertEqual(top["Databases"], 2)

    def test_categories_command(self):
        fs.build_index(self.json_path, self.db)
        cats = dict(fs.get_categories(self.db))
        self.assertEqual(cats, {"Languages": 2, "Libraries": 1, "Databases": 2, "Windows": 1})

    def test_search_no_results_friendly(self):
        fs.build_index(self.json_path, self.db)
        code, out = self._run(["search", "zzzznotfound", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("No results", out)

    def test_search_json_output(self):
        fs.build_index(self.json_path, self.db)
        code, out = self._run(["search", "SQLite", "--db", str(self.db), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data)
        self.assertIn("name", data[0])
        self.assertIn("url", data[0])


if __name__ == "__main__":
    unittest.main()
