# Extraction check: chaos (one planted mistake per document), 2026-10-27 15:24

| Documents | n | Type | Fields correct | Line items F1 | Fully correct | Sent to review | Auto-approved correct | Mistakes caught | False alarms | AI seconds/doc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 60 | 100.0% | 92.3% | 99.2% | 0.0% | 95.0% | 0.0% | 94.7% | 0 | 0.0 |
| invoice | 20 | 100.0% | 94.0% | 99.2% | 0.0% | 90.0% | 0.0% | 88.9% | 0 | 0.0 |
| receipt | 20 | 100.0% | 92.1% | 99.3% | 0.0% | 95.0% | 0.0% | 94.7% | 0 | 0.0 |
| w9 | 20 | 100.0% | 90.0% | – | 0.0% | 100.0% | – | 100.0% | 0 | 0.0 |
| text PDFs | 36 | 100.0% | 92.6% | 98.7% | 0.0% | 100.0% | – | 100.0% | 0 | 0.0 |
| scans + photos | 24 | 100.0% | 91.9% | 100.0% | 0.0% | 87.5% | 0.0% | 87.5% | 0 | 0.0 |

## Fields most often wrong

- w9.business_name: 6 wrong (70% correct)
- receipt.total: 4 wrong (80% correct)
- w9.address: 4 wrong (80% correct)
- invoice.vendor_address: 3 wrong (85% correct)
- receipt.date: 3 wrong (85% correct)
- receipt.merchant_address: 3 wrong (85% correct)
- receipt.tip: 3 wrong (85% correct)
- w9.city_state_zip: 3 wrong (85% correct)
- w9.date_signed: 3 wrong (85% correct)
- w9.name: 3 wrong (85% correct)
- invoice.invoice_number: 2 wrong (90% correct)
- invoice.payment_terms: 2 wrong (90% correct)

## Every wrong field

- inv-01 (clean) `invoice_number`: expected `CL-673-61`, got `CL-673e61` — caught (sent to review)
- inv-02 line items: 3 of 4 matched, 3 returned
- inv-03 (clean) `payment_terms`: expected `Net 30`, got `Net 80` — caught (sent to review)
- inv-04 (clean) `po_number`: expected `PO-48682`, got `eO-48682` — caught (sent to review)
- inv-05 (scan) `invoice_date`: expected `2026-06-23`, got `None` — caught (sent to review)
- inv-06 (clean) `vendor_address`: expected `9117 Commerce Dr, Suite 235, Lansing, MI 48933`, got `9117 Commerce Dr, Suite 235e Lansing, MI 48933` — caught (sent to review)
- inv-07 (clean) `vendor_address`: expected `8878 Mill Rd, Raleigh, NC 27601`, got `8878 Miel Rd, Raleigh, NC 27601` — caught (sent to review)
- inv-08 (clean) `subtotal`: expected `11173.73`, got `11263.73` — caught (sent to review)
- inv-09 (photo) `shipping`: expected `73.6`, got `64.6` — caught (sent to review)
- inv-10 (clean) `payment_terms`: expected `Net 30`, got `Net 38` — caught (sent to review)
- inv-11 (clean) `vendor_name`: expected `Copperleaf Tech Partners`, got `None` — caught (sent to review)
- inv-12 (clean) `total`: expected `16222.13`, got `None` — caught (sent to review)
- inv-13 (clean) `vendor_name`: expected `Paperwell Office Supply Co.`, got `Paperweel Office Supply Co.` — caught (sent to review)
- inv-14 (scan) `subtotal`: expected `281.14`, got `280.14` — caught (sent to review)
- inv-15 (scan) `bill_to_name`: expected `Northstar Charter School`, got `Nortestar Charter School` — **NOT caught**
- inv-16 (photo) `vendor_address`: expected `4414 Ridge Rd, Lansing, MI 48933`, got `None` — **NOT caught**
- inv-17 (photo) `due_date`: expected `2026-03-12`, got `None` — caught (sent to review)
- inv-18 (scan) `invoice_number`: expected `SP-558-80`, got `None` — caught (sent to review)
- inv-19 (clean) `tax`: expected `62.87`, got `72.87` — caught (sent to review)
- inv-20 line items: 25 of 26 matched, 25 returned
- rcp-01 (scan) `merchant_address`: expected `6205 Summit Ave, Suite 331, Eugene, OR 97401`, got `6205 Summit Ave, Suite 331, Eugene,eOR 97401` — caught (sent to review)
- rcp-02 line items: 3 of 4 matched, 3 returned
- rcp-03 (scan) `merchant_name`: expected `SUNRISE GAS & GO`, got `None` — caught (sent to review)
- rcp-04 (photo) `merchant_address`: expected `9751 Commerce Dr, Suite 277, Boise, ID 83702`, got `None` — **NOT caught**
- rcp-05 (photo) `total`: expected `38.0`, got `48.0` — caught (sent to review)
- rcp-06 (clean) `tax`: expected `1.02`, got `10.02` — caught (sent to review)
- rcp-07 (clean) `total`: expected `77.15`, got `None` — caught (sent to review)
- rcp-08 (clean) `date`: expected `2026-02-27`, got `2026-02-28` — caught (sent to review)
- rcp-09 (clean) `date`: expected `2026-08-18`, got `2026-08-19` — caught (sent to review)
- rcp-10 (photo) `tip`: expected `None`, got `5.0` — caught (sent to review)
- rcp-11 (clean) `subtotal`: expected `59.6`, got `69.6` — caught (sent to review)
- rcp-12 (photo) `tax`: expected `5.06`, got `5.96` — caught (sent to review)
- rcp-13 (clean) `date`: expected `2026-01-15`, got `2026-01-16` — caught (sent to review)
- rcp-14 (scan) `merchant_address`: expected `5843 Industrial Pkwy, Suite 224, Fresno, CA 93721`, got `None` — caught (sent to review)
- rcp-15 (scan) `total`: expected `20.03`, got `19.03` — caught (sent to review)
- rcp-16 (clean) `tip`: expected `None`, got `5.0` — caught (sent to review)
- rcp-17 (photo) `card_last4`: expected `5136`, got `5139` — caught (sent to review)
- rcp-18 (photo) `merchant_name`: expected `SUNRISE GAS & GO`, got `SUeRISE GAS & GO` — caught (sent to review)
- rcp-19 (clean) `tip`: expected `None`, got `5.0` — caught (sent to review)
- rcp-20 (clean) `total`: expected `18.0`, got `19.0` — caught (sent to review)
- w9-01 (clean) `city_state_zip`: expected `Eugene, OR 97401`, got `None` — caught (sent to review)
- w9-02 (clean) `city_state_zip`: expected `Boise, ID 83702`, got `None` — caught (sent to review)
- w9-03 (clean) `name`: expected `Lena Dalton`, got `LenaeDalton` — caught (sent to review)
- w9-04 (clean) `date_signed`: expected `2026-01-18`, got `2826-01-18` — caught (sent to review)
- w9-05 (scan) `address`: expected `425 Orchard St`, got `425 Orchare St` — caught (sent to review)
- w9-06 (clean) `date_signed`: expected `2026-03-19`, got `None` — caught (sent to review)
- w9-07 (scan) `address`: expected `3200 Foundry Way`, got `3200 FoundryeWay` — caught (sent to review)
- w9-08 (clean) `name`: expected `Daniel Fairchild`, got `Daniel Fairchied` — caught (sent to review)
- w9-09 (clean) `business_name`: expected `Kowalski Landscaping Family Trust`, got `Kowalske Landscaping Family Trust` — caught (sent to review)
- w9-10 (clean) `name`: expected `James Chowdhury`, got `Jamas Chowdhury` — caught (sent to review)
- w9-11 (scan) `business_name`: expected `Iwata Landscaping Inc.`, got `Iwata Lendscaping Inc.` — caught (sent to review)
- w9-12 (clean) `city_state_zip`: expected `Lansing, MI 48933`, got `Lansing, MI 48983` — caught (sent to review)
- w9-13 (clean) `business_name`: expected `Dalton Consulting Services`, got `Dalton Consulteng Services` — caught (sent to review)
- w9-14 (photo) `tin_last4`: expected `5657`, got `5650` — caught (sent to review)
- w9-15 (clean) `business_name`: expected `Fairchild Design Corp.`, got `Fairchild Desegn Corp.` — caught (sent to review)
- w9-16 (scan) `address`: expected `8043 Ridge Rd, Suite 423`, got `None` — caught (sent to review)
- w9-17 (clean) `address`: expected `4675 Cedar Ln`, got `4675 CedareLn` — caught (sent to review)
- w9-18 (photo) `business_name`: expected `Iwata Catering Services`, got `Iwate Catering Services` — caught (sent to review)
- w9-19 (scan) `business_name`: expected `Alvarez Electric LLC`, got `Alvaeez Electric LLC` — caught (sent to review)
- w9-20 (clean) `date_signed`: expected `2026-09-16`, got `2028-09-16` — caught (sent to review)

## Planted mistakes that were NOT caught

- inv-15 (scan): changed bill_to_name: Northstar Charter School -> Nortestar Charter School
- inv-16 (photo): dropped vendor_address
- rcp-04 (photo): dropped merchant_address

## Sent to review although fully correct (false alarms)

None.

## Sent to review because the document is incomplete (correct to flag)

- w9-05: signed: the form isn't signed
- w9-10: tax_classification: no tax classification box is marked
- w9-16: signed: the form isn't signed
