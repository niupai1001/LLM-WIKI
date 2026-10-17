"""Offline hybrid search invariants, with synthetic archives only."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from station import search
from station.documents import source_version

A, B, SOURCE_A, SAME, SCAN, SUMMARY = (character * 16 for character in "ab1234")

def workspace(root, library_id="candidate-v2"):
    root = Path(root)
    for name in ("raw", "wiki", "wiki_meta", "config", "parsed", "originals"):
        (root / name).mkdir(parents=True, exist_ok=True)

    def url(path):
        pieces = urlsplit(path)
        params = dict(parse_qsl(pieces.query))
        params["library_id"] = library_id
        return urlunsplit(("", "", pieces.path, urlencode(params), pieces.fragment))

    return SimpleNamespace(root=root, raw=root / "raw", wiki=root / "wiki",
                           meta=root / "wiki_meta", config=root / "config",
                           originals=root / "originals", db=root / "state.db",
                           library_id=library_id, url=url)


def put_source(ws, sid, text, **extra):
    (ws.raw / (sid + ".json")).write_text(json.dumps(
        {"source_id": sid, "title": sid, "text": text,
         "content_status": "full-text", **extra}, ensure_ascii=False))


def put_page(ws, slug, title, text, **meta):
    (ws.wiki / (slug + ".md")).write_text("# " + title + "\n\n" + text)
    (ws.meta / (slug + ".json")).write_text(json.dumps(
        {"title": title, **meta}, ensure_ascii=False))


class SearchV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = workspace(self.temp.name)
        self.env = patch.dict(os.environ, {"EMBEDDING_BASE_URL": "", "EMBEDDING_MODEL": "",
                                           "EMBEDDING_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def configure(self, model="synthetic-v1"):
        (self.ws.config / "embedding.json").write_text(json.dumps(
            {"base_url": "https://embedding.invalid/v1", "model": model, "api_key": "secret-test-key"}))

    def test_english_and_chinese_bigrams_and_aliases(self):
        put_page(self.ws, "planning", "Planning", "按工具结果更新下一步计划。",
                 aliases=["反馈规划", "Feedback planning"])
        put_page(self.ws, "unrelated", "Transcription", "RNA mechanism")
        hit = search.search(self.ws, "反馈规划", semantic=False)
        self.assertEqual(hit["results"][0]["slug"], "planning")
        self.assertEqual(search.search(self.ws, "feedback planning", semantic=False)["results"][0]["slug"], "planning")
        self.assertIn("library_id=candidate-v2", hit["results"][0]["url"])
        self.assertEqual(search.tokenize("更新计划 AI-Tools"), ["ai", "tools", "更新", "新计", "计划"])

    def test_incremental_update_delete_and_metadata_hash(self):
        put_page(self.ws, "alpha", "Alpha", "fixed content")
        self.assertEqual(search.rebuild_index(self.ws)["changed"], 1)
        self.assertEqual(search.rebuild_index(self.ws)["changed"], 0)
        put_page(self.ws, "alpha", "Alpha", "fixed content", aliases=["new terminology"])
        self.assertEqual(search.rebuild_index(self.ws)["changed"], 1)
        self.assertEqual(search.search(self.ws, "terminology", semantic=False)["results"][0]["slug"], "alpha")
        (self.ws.wiki / "alpha.md").unlink()
        self.assertEqual(search.rebuild_index(self.ws)["removed"], 1)
        self.assertEqual(search.search(self.ws, "terminology", semantic=False)["results"], [])

    def test_chunks_preserve_overlap_and_deduplicate_entities(self):
        text = "evidence " * 500
        put_source(self.ws, SOURCE_A, text)
        result = search.rebuild_index(self.ws)
        self.assertGreater(result["chunks"], 1)
        with sqlite3.connect(self.ws.root / "search.db") as db:
            rows = db.execute("SELECT start,end,text FROM chunks ORDER BY start").fetchall()
        self.assertEqual(rows[0][1], 1600)
        self.assertEqual(rows[1][0], 1400)
        self.assertEqual(rows[0][2][-200:], rows[1][2][:200])
        found = search.search(self.ws, "evidence", semantic=False)
        self.assertEqual(len(found["results"]), 1)
        self.assertEqual(found["results"][0]["text"], text[found["results"][0]["start"]:found["results"][0]["end"]])

    def test_metadata_refresh_reuses_vectors_only_for_unchanged_text(self):
        self.configure()
        put_page(self.ws,'alpha','Alpha','fixed evidence')
        with patch.object(search,'embed_texts',return_value=[[1,0]]) as embed:
            self.assertEqual(search.rebuild_index(self.ws,embeddings=True)['embedded'],1)
            put_page(self.ws,'alpha','Alpha','fixed evidence',aliases=['new alias'])
            result=search.rebuild_index(self.ws,embeddings=True)
            self.assertEqual(result['embedded'],0)
            self.assertTrue(result['capabilities']['semantic']['ready'])
            self.assertEqual(embed.call_count,1)
            put_page(self.ws,'alpha','Alpha','changed evidence',aliases=['new alias'])
            self.assertEqual(search.rebuild_index(self.ws,embeddings=True)['embedded'],1)
            self.assertEqual(embed.call_count,2)

    def test_library_isolation_with_identical_source_ids(self):
        other = workspace(self.ws.root / "other", "legacy")
        put_source(self.ws, SAME, "Alpha feedback evidence is restricted to candidate")
        put_source(other, SAME, "Beta transcription evidence is restricted to legacy")
        self.assertEqual(search.search(self.ws, "transcription", semantic=False)["results"], [])
        self.assertEqual(search.search(other, "feedback", semantic=False)["results"], [])
        self.assertIn("library_id=legacy", search.search(other, "transcription", semantic=False)["results"][0]["url"])

    def test_symlink_cannot_supply_other_library_original_or_page(self):
        other = workspace(self.ws.root / "other", "legacy")
        put_source(other, A, "secret feedback original belonging to the other library")
        put_page(other, "secret", "Secret feedback", "other library page")
        (self.ws.raw / (A + ".json")).symlink_to(other.raw / (A + ".json"))
        (self.ws.wiki / "secret.md").symlink_to(other.wiki / "secret.md")
        result = search.search(self.ws, "feedback", semantic=False)
        self.assertEqual(result["results"], [])
        self.assertTrue(result["warnings"])

    def test_missing_embedding_configuration_is_explicit_and_no_network(self):
        put_source(self.ws, A, "Original alpha evidence under fixed scope")
        with patch.object(search.urllib.request, "urlopen", side_effect=AssertionError("No network")):
            indexed = search.rebuild_index(self.ws, embeddings=True)
            found = search.search(self.ws, "alpha")
        self.assertFalse(found["semantic_used"])
        self.assertFalse(found["capabilities"]["semantic"]["configured"])
        self.assertTrue(indexed["warnings"])
        self.assertTrue(found["warnings"])

    def test_semantic_recall_backend_fingerprint_and_credential_exclusion(self):
        put_source(self.ws, A, "alpha mechanism with supporting original details")
        put_source(self.ws, B, "beta mechanism with unrelated original details")
        self.configure()
        def vectors(config, texts):
            return [[1, 0] if "alpha" in text or text == "synonym" else [0, 1] for text in texts]
        with patch.object(search, "embed_texts", side_effect=vectors):
            built = search.rebuild_index(self.ws, embeddings=True)
            self.assertEqual(built["embedded"], 2)
            self.assertEqual(search.rebuild_index(self.ws, embeddings=True)["embedded"], 0)
            result = search.search(self.ws, "synonym")
        self.assertEqual(result["results"][0]["source_id"], A)
        self.assertTrue(result["semantic_used"])
        self.assertNotIn("secret-test-key", json.dumps(built))
        self.configure("synthetic-v2")
        self.assertFalse(search.capabilities(self.ws)["semantic"]["ready"])
        with patch.object(search, "embed_texts", side_effect=vectors):
            self.assertEqual(search.rebuild_index(self.ws, embeddings=True)["embedded"], 2)

    def test_invalid_dimensions_and_nonfinite_vectors_fail_without_poisoning_index(self):
        put_source(self.ws, A, "alpha evidence with sufficient original details")
        put_source(self.ws, B, "beta evidence with sufficient original details")
        self.configure()
        for output in ([[[1, 0], [1]]], [[[float("nan"), 0], [1, 0]]]):
            with self.subTest(output=output), patch.object(search, "embed_texts", return_value=output[0]):
                result = search.rebuild_index(self.ws, embeddings=True)
            self.assertEqual(result["embedded"], 0)
            self.assertFalse(result["capabilities"]["semantic"]["ready"])
            self.assertTrue(result["warnings"])
        self.assertEqual(len(search.search(self.ws, "alpha", semantic=False)["results"]), 1)

    def test_cancelled_embedding_task_does_not_commit_the_inflight_batch(self):
        from station import jobs, worker
        put_source(self.ws, A, "alpha original evidence under a fixed scope")
        self.configure()
        with patch.object(search, 'embed_texts', return_value=[[1, 0]]) as embed, \
                patch.object(worker, 'check_cancelled', side_effect=[None, None, jobs.JobConflict('Cancelled')]):
            with self.assertRaises(jobs.JobConflict):
                search.rebuild_index(self.ws, embeddings=True)
        embed.assert_called_once()
        with sqlite3.connect(self.ws.root / 'search.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chunks WHERE vector IS NOT NULL').fetchone()[0], 0)

    def test_query_dimension_mismatch_has_explicit_lexical_fallback(self):
        put_source(self.ws, A, "alpha original evidence and fixed experimental scope")
        self.configure()
        with patch.object(search, "embed_texts", return_value=[[1, 0]]):
            search.rebuild_index(self.ws, embeddings=True)
        with patch.object(search, "embed_texts", return_value=[[1, 0, 0]]):
            result = search.search(self.ws, "alpha")
        self.assertFalse(result["semantic_used"])
        self.assertEqual(result["results"][0]["source_id"], A)
        self.assertTrue(any("ValueError" in warning for warning in result["warnings"]))

    def test_parsed_ocr_is_indexed_with_real_page_and_paragraph_version(self):
        put_source(self.ws, SCAN, "", content_status="missing")
        parsed = {"source_version": source_version(self.ws, SCAN), "status": "partial", "pages": [
            {"number": 4, "label": "iii", "method": "ocr", "paragraphs": [
                {"id": "p4-s2", "start": 30, "end": 82, "text": "扫描页反馈机制保持条件范围，未读取的页面明确标注缺失。"}]},
            {"number": 5, "method": "missing", "text": "", "paragraphs": []}]}
        (self.ws.root / "parsed" / (SCAN + ".json")).write_text(json.dumps(parsed, ensure_ascii=False))
        result = search.search(self.ws, "反馈机制", semantic=False)["results"][0]
        self.assertEqual(result["location"]["page"], 4)
        self.assertEqual(result["location"]["paragraph_id"], "p4-s2")
        self.assertIn("page=4", result["url"])
        self.assertIn("parsed_version=", result["url"])
        self.assertEqual(result["source_version"], parsed["source_version"])

    def test_summary_only_sources_cannot_be_original_evidence(self):
        put_source(self.ws, SUMMARY, "alpha summary without archived original", content_status="summary-only")
        self.assertEqual(search.search(self.ws, "alpha", semantic=False)["results"], [])

    def test_changed_raw_invalidates_cached_parse_without_reading_stale_ocr(self):
        put_source(self.ws, SCAN, "old archived original", content_status="missing")
        parsed = {"source_version": source_version(self.ws, SCAN), "status": "full", "pages": [
            {"number": 1, "method": "ocr", "paragraphs": [{"id": "p1-1", "text": "stale feedback original must not be used after update"}]}]}
        (self.ws.root / "parsed" / (SCAN + ".json")).write_text(json.dumps(parsed))
        self.assertTrue(search.search(self.ws, "feedback", semantic=False)["results"])
        put_source(self.ws, SCAN, "new archived original", content_status="missing")
        result = search.search(self.ws, "feedback", semantic=False)
        self.assertEqual(result["results"], [])
        self.assertTrue(result["warnings"])

    def test_rebuild_clears_publication_dirty_only_for_same_library(self):
        with sqlite3.connect(self.ws.db) as db:
            db.execute("CREATE TABLE publication_state(library_id TEXT PRIMARY KEY,index_dirty INTEGER)")
            db.executemany("INSERT INTO publication_state VALUES(?,1)", [("candidate-v2",), ("legacy",)])
        put_source(self.ws, A, "feedback original evidence with defined experimental conditions")
        search.rebuild_index(self.ws)
        with sqlite3.connect(self.ws.db) as db:
            self.assertEqual(dict(db.execute("SELECT library_id,index_dirty FROM publication_state")),
                             {"candidate-v2": 0, "legacy": 1})

    def test_http_embeddings_response_orders_indices_and_bounds_vectors(self):
        self.configure()
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps({"data": [{"index": 1, "embedding": [0, 2]},
                                             {"index": 0, "embedding": [3, 0]}]}).encode()
        with patch.object(search.urllib.request, "urlopen", return_value=Response()) as request:
            self.assertEqual(search.embed_texts(search._config(self.ws), ["a", "b"]), [[1, 0], [0, 1]])
        self.assertEqual(request.call_args.args[0].full_url, "https://embedding.invalid/v1/embeddings")
        self.assertEqual(json.loads(request.call_args.args[0].data)["model"], "synthetic-v1")

    def test_broken_embedding_config_does_not_disable_lexical_search(self):
        put_source(self.ws, A, "alpha original with well specified scoped evidence")
        (self.ws.config / "embedding.json").write_text("broken JSON")
        with patch.object(search, "embed_texts", side_effect=AssertionError("No network")):
            result = search.search(self.ws, "alpha")
        self.assertEqual(result["results"][0]["source_id"], A)
        self.assertFalse(result["capabilities"]["semantic"]["configured"])
        self.assertIn("unreadable", result["capabilities"]["semantic"]["reason"])

    def test_invalid_query_limit_and_fts_control_text(self):
        put_source(self.ws, A, "alpha scope with original supporting evidence")
        for question in ("", "x" * 4001, None):
            with self.assertRaises(ValueError):
                search.search(self.ws, question)
        with self.assertRaises(ValueError):
            search.search(self.ws, "alpha", limit=True)
        self.assertEqual(search.search(self.ws, '" OR alpha NEAR(*)', semantic=False)["results"][0]["source_id"], A)

    def test_compile_page_candidates_are_not_crowded_out_by_originals(self):
        put_page(self.ws, "planning", "Feedback planning", "This mechanism uses feedback under scoped conditions.")
        for index in range(12):
            put_source(self.ws, f"{index:016x}", "Feedback feedback feedback original " * 10)
        result = search.search(self.ws, "feedback", kind="page", semantic=False)
        self.assertEqual([hit["slug"] for hit in result["results"]], ["planning"])
        self.assertTrue(all(hit["kind"] == "source" for hit in
                            search.search(self.ws, "feedback", kind="source", semantic=False)["results"]))


if __name__ == "__main__":
    unittest.main()
