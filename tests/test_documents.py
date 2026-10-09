"""Reading files to text. All code, no model."""

import pytest

import documents
from documents import DocumentError


def write(tmp_path, name, content, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(content.encode(encoding) if isinstance(content, str) else content)
    return p


class TestPlainText:
    @pytest.mark.parametrize("name", ["a.txt", "a.json", "a.csv", "a.xml", "a.md", "a.tsv"])
    def test_text_formats_read_as_themselves(self, tmp_path, name):
        # The extraction agent reads raw JSON and CSV perfectly well, so these are a decode
        # rather than a parse.
        p = write(tmp_path, name, "Invoice INV-1\nTotal: 100.00")
        text, reader = documents.load(p)
        assert "INV-1" in text
        assert reader == "plain"

    def test_windows_encoded_invoice_is_accepted(self, tmp_path):
        p = write(tmp_path, "fr.txt", "Facture — Société\nTotal: 1 250,00 €", encoding="cp1252")
        text, _ = documents.load(p)
        assert "Société" in text


class TestFailuresAreLegible:
    def test_binary_masquerading_as_text_is_rejected(self, tmp_path):
        """The regression that matters.

        cp1252 maps almost every byte, so a lenient fallback decodes binary without ever
        raising. Before the printable-ratio check this produced mojibake that flowed into the
        extraction prompt as though it were an invoice: a loud error turned into a silent one.
        """
        p = write(tmp_path, "fake.txt", bytes(range(200, 256)) * 4)
        with pytest.raises(DocumentError, match="non-text"):
            documents.load(p)

    def test_image_says_it_needs_ocr(self, tmp_path):
        p = write(tmp_path, "scan.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        with pytest.raises(DocumentError, match="OCR"):
            documents.load(p)

    def test_unsupported_extension_lists_what_is_supported(self, tmp_path):
        p = write(tmp_path, "invoice.rtf", "x")
        with pytest.raises(DocumentError, match=r"no reader for '\.rtf'.*\.pdf"):
            documents.load(p)

    def test_empty_file(self, tmp_path):
        with pytest.raises(DocumentError, match="empty"):
            documents.load(write(tmp_path, "e.txt", ""))

    def test_missing_file(self, tmp_path):
        with pytest.raises(DocumentError, match="does not exist"):
            documents.load(tmp_path / "nope.txt")


class TestPdf:
    def test_reads_the_text_layer(self):
        text, reader = documents.load("data/invoices/invoice_1011.pdf")
        assert reader == "pdf"
        assert "INV-1011" in text
        assert "Summit Manufacturing" in text

    def test_ocr_corruption_is_passed_through_not_corrected(self):
        """invoice_1012.pdf has letter O for zero baked into its text layer.

        Ingestion must not try to fix that. Transcribing faithfully is the contract; reading
        through the corruption is the extraction agent's job, and it does.
        """
        text, _ = documents.load("data/invoices/invoice_1012.pdf")
        assert "26-Jan-2O26" in text

    def test_scanned_pdf_is_distinguished_from_an_empty_one(self, tmp_path, monkeypatch):
        """"Blank" and "scanned" need completely different responses."""
        class Page:
            images = [object()]
            def extract_text(self): return ""
        class Pdf:
            pages = [Page()]
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setitem(__import__("sys").modules, "pdfplumber",
                            type("m", (), {"open": staticmethod(lambda p: Pdf())}))
        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-1.3\n")
        with pytest.raises(DocumentError, match="no extractable text layer.*scan"):
            documents.load(p)


class TestHtml:
    def test_table_cells_become_separated_text(self, tmp_path):
        p = write(tmp_path, "i.html",
                  "<table><tr><td>WidgetA</td><td>$250</td></tr></table><script>x=1</script>")
        text, reader = documents.load(p)
        assert reader == "html"
        assert "WidgetA" in text and "$250" in text
        assert "x=1" not in text   # script contents are not invoice data
