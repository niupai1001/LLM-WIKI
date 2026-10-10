import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from station import collector, common


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = []
        for name, value in {"ROOT": root, "RAW": root / "raw", "WIKI": root / "wiki", "PENDING": root / "pending", "ASSETS": root / "originals", "META": root / "wiki_meta", "DB": root / "state.db"}.items():
            self.patches.append(patch.object(common, name, value))
            if hasattr(collector, name):
                self.patches.append(patch.object(collector, name, value))
        for p in self.patches:
            p.start()
        self.db = common.connect()

    def tearDown(self):
        self.db.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def item(self, sid):
        return common.load_json(common.RAW / f"{sid}.json")

    def test_explicit_original_classified_and_idempotent(self):
        with patch.object(collector, "fetch_bytes", side_effect=AssertionError("must not fetch")):
            record = {"title": "Original", "url": "https://example.org/original", "text": "An authored original.", "category": "research"}
            sid = collector.archive(record, self.db)
            self.assertIsNone(collector.archive(record, self.db))
        self.assertEqual(self.item(sid)["content_status"], "full-text")
        self.assertEqual(self.item(sid)["body_level"], "manual-original")
        collector.set_category(sid, "ai")
        self.assertEqual(self.item(sid)["category"], "ai")
        self.assertEqual(self.db.execute("SELECT category FROM sources WHERE id=?", (sid,)).fetchone()[0], "ai")

    def test_failures_explicit_summary_not_full_text_and_no_credentials(self):
        with patch.object(collector, "fetch_bytes", side_effect=OSError("https://user:secret@server.example")):
            sid = collector.archive({"title": "Paper", "url": "https://arxiv.org/abs/2410.12345", "summary": "<p>Abstract only</p>"}, self.db)
        item = self.item(sid)
        self.assertEqual(item["content_status"], "summary-only")
        self.assertEqual(item["text"], "Abstract only")
        self.assertNotIn("secret", json.dumps(item))

    def test_arxiv_pdf_first_saves_original(self):
        with patch.object(collector, "fetch_bytes", return_value=b"%PDF-mock") as fetch, patch.object(collector, "_pdf_text", return_value="Full paper methods and results"), patch.object(collector, "_pdf_title", return_value="Mock paper title"):
            sid = collector.import_url("https://arxiv.org/abs/2410.12345v2", "ai")
        fetch.assert_called_once_with("https://arxiv.org/pdf/2410.12345v2")
        item = self.item(sid)
        self.assertEqual(item["content_status"], "full-text")
        self.assertEqual(item["content_format"], "pdf")
        self.assertEqual(item["pdf_path"], item["asset_path"])
        self.assertEqual(item["text_format"], "pdf")
        self.assertEqual((common.ASSETS / item["pdf_path"]).read_bytes(), b"%PDF-mock")

    def test_scanned_arxiv_pdf_uses_html_for_llm_but_preserves_pdf_display(self):
        html_document = b'<html><head><title>Paper title</title></head><body class="ltx_document"><section class="ltx_section">Full text</section></body></html>'
        with patch.object(collector, "fetch_bytes", side_effect=[b"%PDF-scanned", html_document]) as fetch, patch.object(collector, "_pdf_text", return_value=""), patch.object(collector, "_pdf_title", return_value=""), patch.object(collector, "_html_text", return_value="Full methods and results"):
            sid = collector.import_url("https://arxiv.org/html/2410.12345v2")
        item = self.item(sid)
        self.assertEqual(item["content_status"], "full-text")
        self.assertEqual(item["text"], "Full methods and results")
        self.assertEqual(item["text_format"], "html")
        self.assertEqual(item["content_format"], "pdf")
        self.assertEqual(item["asset_path"], item["pdf_path"])
        self.assertEqual((common.ASSETS / item["pdf_path"]).read_bytes(), b"%PDF-scanned")
        self.assertEqual((common.ASSETS / item["html_path"]).read_bytes(), html_document)
        self.assertEqual(fetch.call_args_list[0].args[0], "https://arxiv.org/pdf/2410.12345v2")

    def test_pdf_without_text_and_unavailable_html_never_claims_full_text(self):
        with patch.object(collector, "fetch_bytes", side_effect=[b"%PDF-scanned", OSError("HTML unavailable")]), patch.object(collector, "_pdf_text", return_value=""), patch.object(collector, "_pdf_title", return_value=""):
            sid = collector.archive({"title": "Scan", "url": "https://arxiv.org/abs/2410.12345", "summary": "Only the abstract", "kind": "paper"}, self.db)
        item = self.item(sid)
        self.assertEqual(item["content_status"], "summary-only")
        self.assertEqual(item["text"], "Only the abstract")
        self.assertEqual(item["content_format"], "pdf")
        self.assertEqual((common.ASSETS / item["pdf_path"]).read_bytes(), b"%PDF-scanned")

    def test_non_pdf_response_never_archived_as_pdf(self):
        html_document = b'<html><body class="ltx_document"><section class="ltx_section">Full text</section></body></html>'
        with patch.object(collector, "fetch_bytes", side_effect=[b"<html>Rate limit</html>", html_document]), patch.object(collector, "_html_text", return_value="Full original"):
            sid = collector.import_url("https://arxiv.org/abs/2410.12345")
        item = self.item(sid)
        self.assertEqual(item["content_status"], "full-text")
        self.assertNotIn("pdf_path", item)
        self.assertFalse((common.ASSETS / f"{sid}.pdf").exists())
        self.assertEqual(item["content_format"], "html")

    def test_arxiv_landing_page_is_not_full_text(self):
        with patch.object(collector, "fetch_bytes", side_effect=[b"<html>Abstract only</html>", OSError("no pdf")]), patch.object(collector, "_html_text", return_value="Abstract") as extract:
            sid = collector.import_url("https://arxiv.org/abs/2410.12345")
        self.assertEqual(self.item(sid)["content_status"], "missing")
        extract.assert_not_called()

    def test_article_preserves_html(self):
        document = b"<html><body><article><h1>News</h1><p>Original article</p></article></body></html>"
        with patch.object(collector, "fetch_bytes", return_value=document), patch.object(collector, "_html_text", return_value="News\n\nOriginal article"):
            sid = collector.import_url("https://example.org/news", "engineering", "Original news")
        item = self.item(sid)
        self.assertEqual(item["content_status"], "full-text")
        self.assertEqual(item["content_format"], "html")
        self.assertEqual((common.ASSETS / item["asset_path"]).read_bytes(), document)

    def test_file_import_idempotent_and_supported_extensions(self):
        p = Path(self.temp.name) / "source.md"
        p.write_text("# Complete original\n\nHello Wiki.", encoding="utf-8")
        sid = collector.import_file("Wiki source", p, "research")
        self.assertEqual(collector.import_file("Wiki source", p, "research"), sid)
        self.assertEqual(self.item(sid)["body_level"], "manual-original")
        self.assertIsNone(collector.add_note("Wiki source", p))
        self.assertEqual(self.db.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
        bad = Path(self.temp.name) / "unsafe.exe"
        bad.write_bytes(b"text")
        with self.assertRaises(ValueError):
            collector.import_file("Unsafe", bad)

    def test_pdf_import_saves_pdf_and_preserves_scan_without_text(self):
        p = Path(self.temp.name) / "paper.pdf"
        p.write_bytes(b"%PDF-test")
        with patch.object(collector, "_pdf_text", return_value="Complete paper"):
            sid = collector.import_file("Paper", p, "ai")
        self.assertEqual(self.item(sid)["content_format"], "pdf")
        with patch.object(collector, "_pdf_text", return_value=""):
            scanned = collector.import_file("Scanned paper", p)
        item = self.item(scanned)
        self.assertEqual(item["content_status"], "missing")
        self.assertEqual((common.ASSETS / item["asset_path"]).read_bytes(), b"%PDF-test")

    def test_disabled_feed_never_fetches(self):
        with patch.object(collector, "load_json", return_value=[{"enabled": False, "url": "https://example.org/rss", "name": "Disabled"}]), patch.object(collector, "fetch_bytes") as fetch:
            self.assertEqual(collector.ingest(), 0)
        fetch.assert_not_called()

    def test_dns_all_addresses_and_redirect_validated_before_request(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        with patch.object(collector.socket, "getaddrinfo", return_value=[public, private]), patch.object(collector.urllib.request, "build_opener") as opener:
            with self.assertRaises(ValueError):
                collector.fetch_bytes("https://example.org/")
            opener.assert_not_called()
        with self.assertRaises(ValueError):
            collector._PublicRedirect().redirect_request(MagicMock(), None, 302, "Found", {}, "http://127.0.0.1/private")
        for url in ["http://localhost/", "http://[::1]/", "http://10.0.0.2/", "http://user:password@example.org/", "file:///tmp/data"]:
            self.assertFalse(collector.public_http(url))

    def test_pinned_connection_preserves_host_and_prevents_dns_rebinding(self):
        with patch.object(collector, "_validate_destination", return_value=("93.184.216.34",)), patch.object(collector.socket, "create_connection", return_value=MagicMock()) as tcp:
            conn = collector._pinned_connection("https")("example.org", timeout=10)
            conn._create_connection(("example.org", 443), 10)
            self.assertEqual(conn.host, "example.org")
            self.assertEqual(tcp.call_args.args[0], ("93.184.216.34", 443))

    def test_https_handler_uses_verified_context(self):
        handler = collector._PublicHTTPS()
        request = MagicMock()
        with patch.object(handler, "do_open", return_value="response") as opened:
            self.assertEqual(handler.https_open(request), "response")
        self.assertIn("context", opened.call_args.kwargs)

    def test_download_size_bound(self):
        response = MagicMock()
        response.url = "https://example.org/"
        response.headers = {"Content-Length": str(collector.MAX_DOWNLOAD + 1)}
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(collector, "_validate_destination"), patch.object(collector.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                collector.fetch_bytes("https://example.org/")
        response.read.assert_not_called()

    def test_custom_category_config_is_respected(self):
        from station import knowledge
        config = Path(self.temp.name) / "categories.json"
        config.write_text('[{"id":"forestry","name":"森林科学"}]', encoding="utf-8")
        with patch.object(knowledge, "_CONFIG", config):
            p = Path(self.temp.name) / "forest.txt"
            p.write_text("Forest science original", encoding="utf-8")
            sid = collector.import_file("Forest", p, "forestry")
            self.assertEqual(self.item(sid)["category"], "forestry")
            with self.assertRaises(ValueError):
                collector.set_category(sid, "research")

    def test_file_kind_defaults_and_explicit_note(self):
        p = Path(self.temp.name) / "source.md"
        p.write_text("Original document", encoding="utf-8")
        document = collector.import_file("Document", p)
        note = collector.add_note("Personal note", p)
        self.assertEqual(self.item(document)["kind"], "document")
        self.assertEqual(self.item(note)["kind"], "note")
        pdf = Path(self.temp.name) / "paper.pdf"
        pdf.write_bytes(b"%PDF-test")
        with patch.object(collector, "_pdf_text", return_value="Paper text"):
            paper = collector.import_file("Research paper", pdf)
            article = collector.import_file("Uploaded article", pdf, kind="article")
        self.assertEqual(self.item(paper)["kind"], "paper")
        self.assertEqual(self.item(article)["kind"], "article")

    def test_metadata_title_and_arxiv_kind(self):
        article = b"<html><head><title>A real source title</title></head><body><article>Original content</article></body></html>"
        with patch.object(collector, "fetch_bytes", return_value=article), patch.object(collector, "_html_text", return_value="Original content"):
            sid = collector.import_url("https://example.org/original")
        self.assertEqual(self.item(sid)["title"], "A real source title")
        paper_html = b'<html><head><title>Transformer paper</title></head><body class="ltx_document"><section class="ltx_section">Original full text</section></body></html>'
        with patch.object(collector, "fetch_bytes", return_value=paper_html), patch.object(collector, "_html_text", return_value="Complete paper"):
            sid = collector.import_url("https://arxiv.org/abs/2410.54321")
        self.assertEqual(self.item(sid)["kind"], "paper")
        self.assertEqual(self.item(sid)["title"], "Transformer paper")

    def test_refresh_retry_retains_stage_and_then_fulltext_is_idempotent(self):
        url = "https://example.org/retry"
        with patch.object(collector, "fetch_bytes", side_effect=OSError("failure")):
            sid = collector.import_url(url, "ai")
        self.db.execute("UPDATE sources SET stage='rejected' WHERE id=?", (sid,))
        self.db.commit()
        original = {"content_status": "full-text", "text": "Now complete", "content_format": "html", "original_url": url, "original_title": "Recovered title", "body_level": "original-extracted"}
        with patch.object(collector, "_original", return_value=original) as fetched:
            self.assertEqual(collector.import_url(url, "other"), sid)
            fetched.assert_called_once()
        self.assertEqual(self.item(sid)["title"], "Recovered title")
        self.assertEqual(self.item(sid)["category"], "ai")
        self.assertNotIn("fetch_error", self.item(sid))
        self.assertEqual(self.db.execute("SELECT stage FROM sources WHERE id=?", (sid,)).fetchone()[0], "rejected")
        with patch.object(collector, "_original") as fetched:
            self.assertEqual(collector.refresh_source(sid), sid)
            self.assertEqual(collector.import_url(url), sid)
        fetched.assert_not_called()

    def test_refresh_keeps_custom_title_and_summary_on_failure(self):
        url = "https://example.org/retry"
        with patch.object(collector, "fetch_bytes", side_effect=OSError("failure")):
            sid = collector.archive({"url": url, "title": "My curated title", "summary": "Abstract"}, self.db)
            self.assertEqual(collector.refresh_source(sid), sid)
        self.assertEqual(self.item(sid)["content_status"], "summary-only")
        self.assertEqual(self.item(sid)["text"], "Abstract")
        complete = {"content_status": "full-text", "text": "Complete", "content_format": "html", "original_title": "Website title", "original_url": url}
        with patch.object(collector, "_original", return_value=complete):
            collector.refresh_source(sid)
        self.assertEqual(self.item(sid)["title"], "My curated title")

    def test_category_change_blocks_compiling_and_drafted(self):
        sid = collector.archive({"title": "Original", "url": "https://example.org/original", "text": "Complete original", "category": "ai"}, self.db)
        for stage in ["compiling", "drafted"]:
            self.db.execute("UPDATE sources SET stage=?,claim_at=? WHERE id=?", (stage, common.utcnow(), sid))
            self.db.commit()
            with self.assertRaises(ValueError):
                collector.set_category(sid, "research")
            self.assertEqual(self.item(sid)["category"], "ai")
            self.assertEqual(self.db.execute("SELECT category FROM sources WHERE id=?", (sid,)).fetchone()[0], "ai")
        self.db.execute("UPDATE sources SET stage='new' WHERE id=?", (sid,))
        self.db.commit()
        collector.set_category(sid, "research")
        self.assertEqual(self.item(sid)["category"], "research")

    def test_import_rejects_unknown_kind(self):
        p = Path(self.temp.name) / "source.txt"
        p.write_text("Complete text", encoding="utf-8")
        for kind in ["unknown", "", [], None]:
            with self.assertRaises(ValueError):
                collector.import_url("https://example.org/original", kind=kind)
        with self.assertRaises(ValueError):
            collector.import_file("Document", p, kind="unknown")
        with self.assertRaises(ValueError):
            collector.archive({"url": "https://example.org/original", "kind": "unknown", "text": "Original", "title": "Original"}, self.db)

    def test_invalid_pdf_and_non_utf8_become_friendly_value_errors(self):
        from pypdf.errors import PdfReadError
        with patch("pypdf.PdfReader", side_effect=PdfReadError("invalid PDF details")):
            with self.assertRaisesRegex(ValueError, "PDF文件损坏、加密或无法读取"):
                collector._pdf_text(b"invalid")
        p = Path(self.temp.name) / "text.txt"
        p.write_bytes(b"\xff\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            collector.import_file("Non UTF8", p)

    def legacy_arxiv(self, stage="new"):
        sid = collector.archive({"title": "Original", "url": "https://arxiv.org/abs/2410.12345v2", "text": "Existing full paper text", "category": "ai", "kind": "paper"}, self.db)
        item = self.item(sid)
        item.update(content_format="html", asset_path=f"{sid}.html", original_url="https://arxiv.org/html/2410.12345v2", body_level="original-extracted")
        (common.ASSETS / item["asset_path"]).write_bytes(b"<html>Original preserved</html>")
        common.save_json(common.RAW / f"{sid}.json", item)
        self.db.execute("UPDATE sources SET stage=?,claim_at=? WHERE id=?", (stage, common.utcnow(), sid))
        self.db.commit()
        return sid

    def test_ensure_pdf_migrates_legacy_html_preserving_text_category_stage(self):
        sid = self.legacy_arxiv("drafted")
        original = self.item(sid)
        with patch.object(collector, "fetch_bytes", return_value=b"%PDF-layout-original") as fetch:
            result = collector.ensure_source_pdf(sid)
        fetch.assert_called_once_with("https://arxiv.org/pdf/2410.12345v2")
        item = self.item(sid)
        self.assertEqual(result, {"id": sid, "pdf_path": f"{sid}.pdf", "pdf_url": "https://arxiv.org/pdf/2410.12345v2", "downloaded": True})
        for field in ["text", "category", "content_status", "title", "captured_at", "summary"]:
            self.assertEqual(item[field], original[field])
        self.assertEqual(item["text_format"], "html")
        self.assertEqual(item["text_original_url"], original["original_url"])
        self.assertEqual(item["html_path"], original["asset_path"])
        self.assertEqual(item["asset_path"], result["pdf_path"])
        self.assertEqual(self.db.execute("SELECT stage FROM sources WHERE id=?", (sid,)).fetchone()[0], "drafted")
        with patch.object(collector, "fetch_bytes") as fetch:
            repeat = collector.ensure_source_pdf(sid)
        fetch.assert_not_called()
        self.assertFalse(repeat["downloaded"])

    def test_ensure_pdf_prevents_compilation_race_before_and_after_download(self):
        sid = self.legacy_arxiv("compiling")
        with patch.object(collector, "fetch_bytes") as fetch:
            with self.assertRaisesRegex(ValueError, "编写中"):
                collector.ensure_source_pdf(sid)
        fetch.assert_not_called()
        self.db.execute("UPDATE sources SET stage='new' WHERE id=?", (sid,))
        self.db.commit()
        def raced_download(url):
            self.db.execute("UPDATE sources SET stage='compiling',claim_at=? WHERE id=?", (common.utcnow(), sid))
            self.db.commit()
            return b"%PDF-new"
        with patch.object(collector, "fetch_bytes", side_effect=raced_download):
            with self.assertRaisesRegex(ValueError, "编写中"):
                collector.ensure_source_pdf(sid)
        self.assertEqual(self.item(sid)["content_format"], "html")
        self.assertFalse((common.ASSETS / f"{sid}.pdf").exists())

    def test_ensure_pdf_preserves_concurrent_category_updates(self):
        sid = self.legacy_arxiv()
        def download(url):
            collector.set_category(sid, "engineering")
            return b"%PDF-layout"
        with patch.object(collector, "fetch_bytes", side_effect=download):
            collector.ensure_source_pdf(sid)
        self.assertEqual(self.item(sid)["category"], "engineering")

    def test_ensure_pdf_rolls_back_asset_and_json_on_write_failure(self):
        sid = self.legacy_arxiv()
        old = (common.RAW / f"{sid}.json").read_bytes()
        def partial_write(path, item):
            path.write_text("partial metadata", encoding="utf-8")
            raise OSError("disk error")
        with patch.object(collector, "fetch_bytes", return_value=b"%PDF-layout"), patch.object(collector, "save_json", side_effect=partial_write):
            with self.assertRaises(OSError):
                collector.ensure_source_pdf(sid)
        self.assertEqual((common.RAW / f"{sid}.json").read_bytes(), old)
        self.assertFalse((common.ASSETS / f"{sid}.pdf").exists())

    def test_ensure_pdf_rejects_invalid_response_without_changes(self):
        sid = self.legacy_arxiv()
        old = self.item(sid)
        with patch.object(collector, "fetch_bytes", return_value=b"<html>Abstract</html>"):
            with self.assertRaises(ValueError):
                collector.ensure_source_pdf(sid)
        self.assertEqual(self.item(sid), old)
        self.assertFalse((common.ASSETS / f"{sid}.pdf").exists())

    def test_ensure_pdf_promotes_legacy_local_pdf_without_network(self):
        p = Path(self.temp.name) / "paper.pdf"
        p.write_bytes(b"%PDF-original")
        with patch.object(collector, "_pdf_text", return_value="Full paper"):
            sid = collector.import_file("Uploaded", p)
        item = self.item(sid)
        item.pop("pdf_path")
        common.save_json(common.RAW / f"{sid}.json", item)
        with patch.object(collector, "fetch_bytes") as fetch:
            result = collector.ensure_source_pdf(sid)
        fetch.assert_not_called()
        self.assertFalse(result["downloaded"])
        self.assertEqual(self.item(sid)["pdf_path"], item["asset_path"])

    def test_invalid_category(self):
        with self.assertRaises(ValueError):
            collector.import_url("https://example.org/", "unknown")


if __name__ == "__main__":
    unittest.main()
