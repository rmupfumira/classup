"""Invoice PDF generation — branded fpdf2 layout.

Pure function, no DB. Pins that the PDF renders for the shapes billing
actually sends, including the edge cases that used to crash core-font
fpdf renders (long descriptions, non-latin-1 punctuation, many rows).
"""

from __future__ import annotations

import re

from app.services.invoice_pdf import generate_invoice_pdf


def _items(n: int, desc: str = "Term 3 tuition fees") -> list[dict]:
    return [
        {"description": f"{desc} #{i}", "quantity": 1, "unit_amount": 100, "total_amount": 100}
        for i in range(n)
    ]


def _render(**overrides) -> bytes:
    kwargs = dict(
        invoice_number="INV-2026-0001",
        student_name="Tinashe Mahachi",
        due_date="15 Dec 2026",
        total_amount="300.00",
        currency="USD",
        line_items=_items(3),
        tenant_name="Shumba High School",
        tenant_address="14 Samora Machel Ave\nHarare",
        tenant_phone="+263 77 123 4567",
        tenant_email="accounts@shumba.co.zw",
        banking_details="Bank: CBZ\nAccount: 0123456789",
        payment_instructions="Use the invoice number as reference.",
    )
    kwargs.update(overrides)
    return generate_invoice_pdf(**kwargs)


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


class TestInvoicePdf:
    def test_renders_valid_pdf(self):
        pdf = _render()
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 2000

    def test_minimal_fields_only(self):
        pdf = _render(
            tenant_address=None, tenant_phone=None, tenant_email=None,
            banking_details=None, payment_instructions=None,
        )
        assert pdf.startswith(b"%PDF")

    def test_long_description_is_truncated_not_crashing(self):
        pdf = _render(line_items=_items(1, desc="Very long fee description " * 20))
        assert pdf.startswith(b"%PDF")

    def test_non_latin1_punctuation_does_not_crash(self):
        pdf = _render(
            tenant_name="St Mary’s — Harare",
            student_name="Ché Ndlovu \U0001F393",
            line_items=_items(1, desc="Fees – Term 3 “boarding”"),
        )
        assert pdf.startswith(b"%PDF")

    def test_many_line_items_spill_onto_extra_pages(self):
        pdf = _render(line_items=_items(80), total_amount="8000.00")
        assert _page_count(pdf) >= 2
