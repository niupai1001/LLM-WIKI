import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from station import documents
from station.workspaces import Workspace


SID = "1234567890abcdef"
CAPABILITY = {"pdftoppm": "/fake/pdftoppm", "tesseract": "/fake/tesseract",
              "languages": ["eng", "chi_sim", "chi_tra"], "missing_languages": [], "warnings": []}
TSV = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" + \
      "5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t97\tEvidence\n" + \
      "5\t1\t1\t1\t1\t2\t45\t20\t25\t10\t90\t文本\n" + \
      "5\t1\t2\t1\t1\t1\t10\t60\t30\t10\t80\t条件\n"


class DocumentParsingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ws = Workspace("candidate-v2", Path(self.temporary.name))
        self.ws.raw.mkdir()
        self.ws.originals.mkdir()

    def archive(self, text="", **fields):
        record = {"id": SID, "title": "研究资料", "kind": "paper", "category": "research",
                  "text": text, "content_status": "full-text" if text else "missing", "stage": "approved", **fields}
        path = self.ws.raw / (SID + ".json")
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path

    def pdf(self, texts):
        path = self.ws.originals / "paper.pdf"
        writer = PdfWriter()
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        for text in texts:
            page = writer.add_blank_page(width=400, height=400)
            if text:
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
                stream = DecodedStreamObject()
                stream.set_data(("BT /F1 10 Tf 20 300 Td (" + text + ") Tj ET").encode())
                page[NameObject("/Contents")] = writer._add_object(stream)
        writer.set_page_label(0, len(texts) - 1, style="/r")
        writer.write(path)
        self.archive(asset_path="paper.pdf", pdf_path="paper.pdf", content_format="pdf")
        return path

    def test_pdf_content_skipped_warning_is_visible_as_partial_read(self):
        import logging
        self.pdf(['A readable evidence paragraph with explicit conditions. '*5])
        def warned_extract(*args,**kwargs):
            logging.getLogger('pypdf._page').warning('Exceeded 5000 form XObject invocations; further form content is skipped.')
            return 'A readable evidence paragraph with explicit conditions. '*5
        with patch('pypdf._page.PageObject.extract_text',side_effect=warned_extract):
            parsed=documents.parse_source(self.ws,SID,ocr=False)
        self.assertEqual(parsed['status'],'partial')
        self.assertIn('pdf-parser-content-incomplete',parsed['pages'][0]['warnings'])
        self.assertEqual(parsed['coverage']['partial_pages'],[1])
        self.assertTrue(parsed['paragraphs'])

    def test_text_pdf_physical_pages_labels_and_exact_offsets(self):
        original = self.pdf(["Evidence conditions " * 10, "Second page result " * 10])
        before = original.read_bytes()
        parsed = documents.parse_source(self.ws, SID, ocr=False)
        self.assertEqual(parsed["status"], "full")
        self.assertEqual([(p["number"], p["label"]) for p in parsed["pages"]], [(1, "i"), (2, "ii")])
        for paragraph in parsed["paragraphs"]:
            page = parsed["pages"][paragraph["page"] - 1]
            self.assertEqual(paragraph["text"], page["text"][paragraph["start"]:paragraph["end"]])
        self.assertEqual(original.read_bytes(), before)

    def test_scanned_pdf_ocr_retains_token_boxes_and_recognition_quality(self):
        self.pdf([""])
        text, tokens = documents._read_tsv(TSV)
        with patch.object(documents, "_ocr_capability", return_value=CAPABILITY), \
                patch.object(documents, "_ocr_page", return_value=(text, tokens)) as recognize:
            parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(parsed["status"], "full")
        self.assertEqual(parsed["pages"][0]["method"], "tesseract")
        self.assertEqual(parsed["pages"][0]["dpi"], 300)
        self.assertEqual(parsed["paragraphs"][0]["bbox"], [10, 20, 60, 10])
        self.assertEqual(parsed["paragraphs"][0]["ocr_confidence"], 93.5)
        self.assertEqual(len(parsed["paragraphs"]), 2)
        recognize.assert_called_once()
        for token in tokens:
            self.assertEqual(token["text"], text[token["start"]:token["end"]])

    def test_missing_chinese_language_marks_partial_and_new_capability_invalidates_cache(self):
        self.pdf([""])
        missing = {**CAPABILITY, "languages": ["eng"], "missing_languages": ["chi_sim", "chi_tra"],
                   "warnings": ["ocr-languages-missing:chi_sim,chi_tra"]}
        text, tokens = documents._read_tsv(TSV)
        with patch.object(documents, "_ocr_capability", return_value=missing), \
                patch.object(documents, "_ocr_page", return_value=(text, tokens)):
            first = documents.parse_source(self.ws, SID)
        self.assertEqual(first["status"], "partial")
        self.assertIn("ocr-languages-missing", first["pages"][0]["warnings"][0])
        with patch.object(documents, "_ocr_capability", return_value=CAPABILITY), \
                patch.object(documents, "_ocr_page", return_value=(text, tokens)) as recognize:
            second = documents.parse_source(self.ws, SID)
        self.assertEqual(second["status"], "full")
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        recognize.assert_called_once()

    def test_partial_pdf_keeps_failed_page_explicit(self):
        self.pdf(["Digitally readable paragraph " * 10, ""])
        with patch.object(documents, "_ocr_capability", return_value=CAPABILITY), \
                patch.object(documents, "_ocr_page", side_effect=subprocess.TimeoutExpired("tesseract", 90)):
            parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(parsed["status"], "partial")
        self.assertEqual(parsed["coverage"]["missing_pages"], [2])
        self.assertEqual(parsed["pages"][1]["paragraphs"], [])
        self.assertIn("ocr-process-failed", parsed["pages"][1]["warnings"])

    def test_corrupt_pdf_font_mapping_is_serializable_and_marks_partial(self):
        self.pdf(["Unused page text " * 10])
        malformed="Readable conditions " * 10 + "\ud800" + " still readable."
        with patch('pypdf._page.PageObject.extract_text',return_value=malformed):
            parsed=documents.parse_source(self.ws,SID,ocr=False)
        self.assertEqual(parsed['status'],'partial')
        self.assertIn('pdf-font-map-unreadable-characters',parsed['pages'][0]['warnings'])
        self.assertIn('\ufffd',parsed['pages'][0]['text'])
        saved=json.loads((self.ws.root/'parsed'/(SID+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['pages'][0]['text'],parsed['pages'][0]['text'])

    def test_ocr_subprocess_dpi_languages_timeouts_and_size_guard(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == CAPABILITY["pdftoppm"]:
                Path(command[-1] + ".png").write_bytes(b"fake raster")
                return subprocess.CompletedProcess(command, 0, b"", b"")
            return subprocess.CompletedProcess(command, 0, TSV, "")
        with patch.object(documents.subprocess, "run", side_effect=run):
            text, tokens = documents._ocr_page(Path("archived.pdf"), 3, 100, 100, CAPABILITY)
        self.assertIn("Evidence", text)
        self.assertEqual(calls[0][0][calls[0][0].index("-r") + 1], "300")
        self.assertEqual(calls[1][0][calls[1][0].index("-l") + 1], "eng+chi_sim+chi_tra")
        self.assertEqual([c[1]["timeout"] for c in calls], [90, 90])
        with patch.object(documents.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "size-limit"):
                documents._ocr_page(Path("archived.pdf"), 1, 100000, 100000, CAPABILITY)
            run.assert_not_called()

    def test_markdown_single_terminal_newline_and_long_paragraph_preserve_offsets(self):
        text = "# Title\n\n条件与结果。\n\n" + "Long exact evidence " * 200 + "\n"
        raw = self.archive(text)
        before = raw.read_bytes()
        parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(parsed["status"], "full")
        self.assertEqual(parsed["paragraphs"][1]["text"], "条件与结果。")
        self.assertTrue(all(len(p["text"]) <= documents.MAX_PARAGRAPH_CHARACTERS for p in parsed["paragraphs"]))
        for paragraph in parsed["paragraphs"]:
            self.assertEqual(paragraph["text"], text[paragraph["start"]:paragraph["end"]])
        self.assertEqual(raw.read_bytes(), before)

    def test_html_archive_fallback_omits_scripts_and_locates_plaintext(self):
        original = self.ws.originals / "page.html"
        original.write_text("<h1>Study</h1><script>secret instruction</script><p>原文结果和条件。</p>")
        self.archive(asset_path="page.html", content_format="html")
        parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(parsed["status"], "full")
        self.assertNotIn("secret instruction", parsed["pages"][0]["text"])
        self.assertEqual(parsed["pages"][0]["method"], "html-text")

    def test_summary_is_not_promoted_to_original(self):
        self.archive("This is feed summary only", content_status="summary-only")
        parsed = documents.parse_source(self.ws, SID)
        self.assertEqual(parsed["status"], "missing")
        self.assertFalse(parsed["paragraphs"])

    def test_source_version_agrees_with_parse_and_invalidates_after_text_change(self):
        path = self.archive("Original evidence.")
        first = documents.parse_source(self.ws, SID)
        self.assertEqual(documents.source_version(self.ws, SID), first["source_version"])
        record = json.loads(path.read_text())
        record["stage"] = "new"
        path.write_text(json.dumps(record))
        self.assertEqual(documents.source_version(self.ws, SID), first["source_version"])
        record["text"] = "Different original evidence."
        path.write_text(json.dumps(record))
        second = documents.parse_source(self.ws, SID)
        self.assertNotEqual(first["source_version"], second["source_version"])

    def test_cross_library_asset_escape_is_rejected(self):
        self.archive(asset_path="../../private.txt")
        with self.assertRaisesRegex(ValueError, "outside"):
            documents.parse_source(self.ws, SID)
        with self.assertRaisesRegex(ValueError, "source ID"):
            documents.parse_source(self.ws, "../source")


if __name__ == "__main__":
    unittest.main()
