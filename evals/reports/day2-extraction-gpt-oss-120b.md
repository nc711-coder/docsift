# Extraction check: openai/gpt-oss-120b, 2026-10-27 15:29

| Documents | n | Type | Fields correct | Line items F1 | Fully correct | Sent to review | Auto-approved correct | Mistakes caught | False alarms | AI seconds/doc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 60 | 100.0% | 97.7% | 100.0% | 78.3% | 30.0% | 100.0% | 100.0% | 4 | 1.7 |
| invoice | 20 | 100.0% | 98.7% | 100.0% | 85.0% | 20.0% | 100.0% | 100.0% | 1 | 2.2 |
| receipt | 20 | 100.0% | 97.9% | 100.0% | 75.0% | 35.0% | 100.0% | 100.0% | 2 | 1.7 |
| w9 | 20 | 100.0% | 96.0% | – | 75.0% | 35.0% | 100.0% | 100.0% | 1 | 1.3 |
| text PDFs | 36 | 100.0% | 100.0% | 100.0% | 100.0% | 2.8% | 100.0% | – | 0 | 1.8 |
| scans + photos | 24 | 100.0% | 94.2% | 100.0% | 45.8% | 70.8% | 100.0% | 100.0% | 4 | 1.5 |

## Fields most often wrong

- receipt.card_last4: 4 wrong (80% correct)
- w9.tin_last4: 3 wrong (85% correct)
- invoice.vendor_address: 2 wrong (90% correct)
- w9.signed: 2 wrong (90% correct)
- invoice.due_date: 1 wrong (95% correct)
- invoice.vendor_name: 1 wrong (95% correct)
- receipt.subtotal: 1 wrong (95% correct)
- w9.business_name: 1 wrong (95% correct)
- w9.tax_classification: 1 wrong (95% correct)
- w9.tin_type: 1 wrong (95% correct)

## Every wrong field

- inv-09 (photo) `vendor_address`: expected `2207 Lakeview Blvd, Suite 422, Boise, ID 83702`, got `2207 Lakeview Blvd, Suite 422, Bose, ID 83702` — caught (sent to review)
- inv-16 (photo) `due_date`: expected `2026-07-21`, got `None` — caught (sent to review)
- inv-17 (photo) `vendor_name`: expected `Copperleaf Tech Partners`, got `None` — caught (sent to review)
- inv-17 (photo) `vendor_address`: expected `8545 Industrial Pkwy, Columbus, OH 43215`, got `None` — caught (sent to review)
- rcp-03 (scan) `card_last4`: expected `5599`, got `None` — caught (sent to review)
- rcp-05 (photo) `subtotal`: expected `None`, got `38.0` — caught (sent to review)
- rcp-10 (photo) `card_last4`: expected `2608`, got `0608` — caught (sent to review)
- rcp-14 (scan) `card_last4`: expected `9112`, got `None` — caught (sent to review)
- rcp-17 (photo) `card_last4`: expected `5136`, got `None` — caught (sent to review)
- w9-05 (scan) `signed`: expected `False`, got `True` — caught (sent to review)
- w9-11 (scan) `tin_last4`: expected `0271`, got `5271` — caught (sent to review)
- w9-16 (scan) `tin_last4`: expected `7627`, got `2762` — caught (sent to review)
- w9-16 (scan) `signed`: expected `False`, got `True` — caught (sent to review)
- w9-18 (photo) `business_name`: expected `Iwata Catering Services`, got `lwata Catering Services` — caught (sent to review)
- w9-18 (photo) `tax_classification`: expected `individual_sole_proprietor`, got `None` — caught (sent to review)
- w9-19 (scan) `tin_type`: expected `EIN`, got `None` — caught (sent to review)
- w9-19 (scan) `tin_last4`: expected `1270`, got `None` — caught (sent to review)

## Sent to review although fully correct (false alarms)

- inv-14 (scan): vendor_name: OCR was unsure of 'Ironside' (confidence 6); check it
- rcp-01 (scan): card_last4: OCR was unsure of '8370' (confidence 49); check it
- rcp-15 (scan): card_last4: OCR was unsure of '5058' (confidence 57); check it
- w9-14 (photo): address: '8353 Lakeview Blvd' isn't on the page (misread, or not there)

## Sent to review because the document is incomplete (correct to flag)

- w9-10: tax_classification: no tax classification box is marked
- w9-18: tax_classification: no tax classification box is marked
