# n8n: approved documents to Google Sheets, reviews by email

```
DocSift ── POST + X-Webhook-Secret ──▶ n8n webhook /webhook/docsift ─▶ Which event?
                                                         ├─ document.approved     ─▶ row ─▶ Google Sheet (tab per type)
                                                         └─ document.needs_review ─▶ email with a link to the review page
```

The app builds each Sheet row itself (`docsift/notify.py`, columns per type), so the workflow only appends it.
These steps reuse the n8n and Google sign-in you set up for Clinic Concierge (its `automation/README.md` and
`DEPLOY.md` Part 8 explain those from scratch).

## 1. The Google Sheet

1. Create a new Google Sheet called **DocSift**.
2. Make three tabs named exactly **Invoices**, **Receipts** and **W-9s**.
3. Paste the header row into row 1 of each tab, from `sheet_headers_Invoices.csv`, `sheet_headers_Receipts.csv`
   and `sheet_headers_W-9s.csv` (open each in Notepad, copy, paste into cell A1, then
   **Data → Split text to columns**).
4. Copy the Sheet's address from the browser.

## 2. The workflow

1. In n8n: **Create workflow → ⋯ → Import from File…** and pick `automation/n8n_docsift.json`.
2. **Settings** step: `review_email` (where "needs a look" emails go), `sheet_url` (the Sheet's address) and
   `alert_email` (who hears when the app fails for a visitor).
3. **Add to the Sheet**: choose your existing **Google Sheets** credential.
4. **Email the reviewer** and **Email the owner (failure)**: choose your existing **Gmail** credential.
5. **DocSift events**: under Header Auth choose your existing **X-Webhook-Secret** credential (the same secret as
   Clinic Concierge is fine), and copy the **Production URL** (`.../webhook/docsift`).
6. **Ctrl+S**, then **Publish**.

## 3. Point the app at it

In DocSift's `.env`:
```
EVENT_WEBHOOK_URL=https://n8n-XXXX.us-west1.run.app/webhook/docsift
EVENT_WEBHOOK_SECRET=the-same-secret
```

Test without the app: `python -m docsift.notify approved` sends a sample Sheet row,
`python -m docsift.notify review` a sample review email, and `python -m docsift.notify failure` a sample
failure alert (the email you get when a visitor hits an error).

## Troubleshooting

| What you see | Fix |
| --- | --- |
| HTTP 403 | The secret in `.env` doesn't match the Header Auth credential. |
| HTTP 404 | The workflow isn't published, or the URL isn't the Production URL. |
| "Sheet with name ... not found" in n8n | A tab name differs: exactly `Invoices`, `Receipts`, `W-9s`. |
| Row lands but columns are empty | Row 1 of that tab must hold the headers, spelled as in the CSV. |
