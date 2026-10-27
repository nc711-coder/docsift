# Extraction check: oracle (perfect AI), 2026-10-27 15:23

| Documents | n | Type | Fields correct | Line items F1 | Fully correct | Sent to review | Auto-approved correct | Mistakes caught | False alarms | AI seconds/doc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 60 | 100.0% | 100.0% | 100.0% | 100.0% | 26.7% | 100.0% | – | 14 | 0.0 |
| invoice | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 15.0% | 100.0% | – | 3 | 0.0 |
| receipt | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 35.0% | 100.0% | – | 7 | 0.0 |
| w9 | 20 | 100.0% | 100.0% | – | 100.0% | 30.0% | 100.0% | – | 4 | 0.0 |
| text PDFs | 36 | 100.0% | 100.0% | 100.0% | 100.0% | 2.8% | 100.0% | – | 0 | 0.0 |
| scans + photos | 24 | 100.0% | 100.0% | 100.0% | 100.0% | 62.5% | 100.0% | – | 14 | 0.0 |

## Fields most often wrong

None.

## Every wrong field


## Sent to review although fully correct (false alarms)

- inv-09 (photo): vendor_address: '2207 Lakeview Blvd, Suite 422, Boise, ID 83702' isn't on the page (misread, or not there); bill_to_address: '7876 Lakeview Blvd, Savannah, GA 31401' only roughly matches the page (OCR); check it
- inv-14 (scan): vendor_name: OCR was unsure of 'Ironside' (confidence 25); check it
- inv-17 (photo): vendor_name: 'Copperleaf Tech Partners' isn't on the page (misread, or not there); vendor_address: '8545 Industrial Pkwy, Columbus, OH 43215' isn't on the page (misread, or not there); bill_to_name: OCR was unsure of 'Bluewater' (confidence 22); check it
- rcp-01 (scan): card_last4: OCR was unsure of '8370' (confidence 64); check it
- rcp-03 (scan): card_last4: '5599' isn't on the page (misread, or not there)
- rcp-05 (photo): card_last4: OCR was unsure of '0557' (confidence 0); check it
- rcp-10 (photo): card_last4: '2608' isn't on the page (misread, or not there)
- rcp-14 (scan): card_last4: '9112' isn't on the page (misread, or not there)
- rcp-15 (scan): card_last4: OCR was unsure of '5058' (confidence 71); check it
- rcp-17 (photo): card_last4: '5136' isn't on the page (misread, or not there)
- w9-14 (photo): address: '8353 Lakeview Blvd' only roughly matches the page (OCR); check it
- w9-16 (scan): tin_last4: the boxes end in 2762, not 7627
- w9-18 (photo): the page was hard to read (low OCR confidence); business_name: 'Iwata Catering Services' only roughly matches the page (OCR); check it
- w9-19 (scan): tin_last4: the boxes read as 11 digits, not 9: OCR misread some

## Sent to review because the document is incomplete (correct to flag)

- w9-05: signed: the form isn't signed
- w9-10: tax_classification: no tax classification box is marked
- w9-16: signed: the form isn't signed
