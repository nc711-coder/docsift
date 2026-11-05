

def test_line_breaks_in_text_become_commas():
    # The review page showed "7454 Lakeview BlvdRaleigh, NC 27601": a one-line box drops the line break.
    from docsift.schemas import Receipt
    r = Receipt(merchant_address="7454 Lakeview Blvd\nRaleigh, NC 27601")
    assert r.merchant_address == "7454 Lakeview Blvd, Raleigh, NC 27601"
