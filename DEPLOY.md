# Deploying the live demo (Google Cloud Run)

DocSift runs next to Clinic Concierge, reusing everything set up for it (its `DEPLOY.md` explains each piece from
scratch): the Google Cloud project and billing alert, the `gcloud` tool, the Groq key (paid plan), the Supabase
pooler address and the cloud n8n.

| Piece | Where | Cost |
| --- | --- | --- |
| App, web page, OCR | Cloud Run service `docsift`, `us-west1`, scales to zero | $0 within the free tier |
| Extraction | Groq `openai/gpt-oss-120b` | about 0.1-0.3¢ per document |
| Stored results | Supabase, schema `docsift` (same project) | free tier |
| Sheet rows and emails | the cloud n8n, workflow `n8n_docsift.json` | $0 within the free tier |

## 1. The settings file

```powershell
Copy-Item deploy.env.example.yaml deploy.env.yaml
notepad deploy.env.yaml
```
Fill in `GROQ_API_KEY`, `DATABASE_URL`, `EVENT_WEBHOOK_URL` and `EVENT_WEBHOOK_SECRET` with the same values as
your `.env`, in the format `NAME: "value"` (a colon and a space, not `=`). `deploy.env.yaml` is never committed or
uploaded with the code.

## 2. Deploy

From the project folder:
```powershell
gcloud run deploy docsift `
  --source . `
  --region us-west1 `
  --allow-unauthenticated `
  --memory 2Gi --cpu 1 `
  --min-instances 0 --max-instances 1 `
  --concurrency 4 --timeout 120 `
  --cpu-boost `
  --env-vars-file deploy.env.yaml
```
The first build takes about 5 minutes (it installs Tesseract into the image). It ends with
`Service URL: https://docsift-....run.app`: the live demo.

| Option | Why |
| --- | --- |
| `--concurrency 4` | OCR keeps the CPU busy for a few seconds per scan; 4 at once is plenty on 1 CPU. |
| `--max-instances 1` | Caps cost, and keeps the upload limits accurate (they're counted in memory). |
| `--min-instances 0` | Free while idle; the first visit after a pause takes ~20 s to wake up. |

## 3. Check it

1. `https://YOUR-URL/health` shows `{"status":"ok","store":"postgres"}` ("memory" means `DATABASE_URL` is missing).
2. Open `https://YOUR-URL/`, click a sample (try **W-9 · phone photo**, which uses OCR), then approve it: a row
   appears in the Sheet. A document that needs review sends you the email, with a link to the live review page.
3. Something wrong? `gcloud run services logs read docsift --region us-west1 --limit 50`

## 4. Update the n8n workflow (failure alerts)

The Day 4 workflow adds a third branch: an email to you when a visitor gets an error. Archive the old DocSift
workflow in n8n, import the new `automation/n8n_docsift.json`, fill in **Settings** (now with `alert_email`),
pick the credentials again (Sheets, both Gmail steps, Header Auth) and **Publish**. Test it from your PC:
`python -m docsift.notify failure`.

## Updating and costs

- **Changed code?** Run the same deploy command again.
- **Costs:** Groq is the only per-use cost (about a tenth to a third of a cent per document);
  `UPLOADS_PER_DAY` (200) caps what strangers can spend at well under $1 a day.
- **Old images:** each deploy stores an image of about 600 MB; delete old ones now and then in
  **Artifact Registry → cloud-run-source-deploy → docsift**.
- **Supabase pauses** free projects after a week without activity: if the demo shows "The document store isn't
  available", open the Supabase dashboard and click **Restore project**.
