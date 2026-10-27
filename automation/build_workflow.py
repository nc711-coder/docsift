"""Writes n8n_docsift.json (the workflow to import into n8n). Kept as code so changes are reviewable:
    python automation/build_workflow.py

    DocSift events ─▶ Settings ─▶ Which event?
                                   ├─ approved     ─▶ Sheet row (code) ─▶ append to the tab named in the event
                                   └─ needs review ─▶ Review email ─▶ Gmail
"""
import json
import uuid
from pathlib import Path

NS = uuid.UUID("0b6c7c1e-2f7e-4d2a-9a64-5d3f6c1b8e21")
def uid(name): return str(uuid.uuid5(NS, name))            # stable ids: the file only changes when the workflow does
EVENT = "$('DocSift events').item.json.body"
D = EVENT + ".data"
SETTING = "$('Settings').item.json"


def setnode(name, pos, fields):
    return {"id": uid(name), "name": name, "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": pos,
            "parameters": {"assignments": {"assignments": [
                {"id": uid(name + k), "name": k, "value": v, "type": "string"} for k, v in fields.items()]},
                "includeOtherFields": False, "options": {}}}


def rule(output, event):
    return {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                           "conditions": [{"id": uid(output), "leftValue": "={{ " + EVENT + ".event }}", "rightValue": event,
                                           "operator": {"type": "string", "operation": "equals"}}],
                           "combinator": "and"},
            "renameOutput": True, "outputKey": output}


nodes = [
    {"id": uid("webhook"), "name": "DocSift events", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
     "position": [0, 300], "webhookId": uid("webhook-path"),
     # reply when the work is done: on Cloud Run the server only has CPU while answering a request
     "parameters": {"httpMethod": "POST", "path": "docsift", "authentication": "headerAuth",
                    "responseMode": "lastNode", "options": {}}},
    setnode("Settings", [220, 300], {
        "review_email": "you@example.com",                                         # <- who reviews documents
        "sheet_url": "https://docs.google.com/spreadsheets/d/PASTE-YOUR-SHEET-ID/edit",   # <- your Google Sheet
    }),
    {"id": uid("switch"), "name": "Which event?", "type": "n8n-nodes-base.switch", "typeVersion": 3.2,
     "position": [440, 300],
     "parameters": {"rules": {"values": [rule("Approved", "document.approved"),
                                         rule("Needs review", "document.needs_review")]}, "options": {}}},
    {"id": uid("row"), "name": "Sheet row", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [700, 180],
     # the app sends the row ready-made (columns per document type, in order): pass it on as the item
     "parameters": {"jsCode": "return [{ json: $('DocSift events').first().json.body.data.row }];"}},
    {"id": uid("sheet"), "name": "Add to the Sheet", "type": "n8n-nodes-base.googleSheets", "typeVersion": 4.5,
     "position": [940, 180],
     "parameters": {"operation": "append",
                    "documentId": {"__rl": True, "mode": "url", "value": "={{ " + SETTING + ".sheet_url }}"},
                    # the tab: Invoices, Receipts or W-9s, chosen by the app
                    "sheetName": {"__rl": True, "mode": "name", "value": "={{ " + D + ".sheet }}"},
                    "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
                    "options": {}}},
    setnode("Review email", [700, 420], {
        "to": "={{ " + SETTING + ".review_email }}",
        "subject": f"=Document needs a look: {{{{ {D}.filename }}}}",
        "text": ("=A document uploaded to DocSift needs a quick check before it's approved.\n\n"
                 f"File:  {{{{ {D}.filename }}}} ({{{{ {D}.type }}}})\n\n"
                 f"Why:\n{{{{ {D}.reasons.map(r => '- ' + r).join('\\n') }}}}\n\n"
                 f"Review and approve it here: {{{{ {D}.link }}}}"),
    }),
    {"id": uid("gmail"), "name": "Email the reviewer", "type": "n8n-nodes-base.gmail", "typeVersion": 2.1,
     "position": [940, 420], "webhookId": uid("gmailhook"),
     "parameters": {"sendTo": "={{ $json.to }}", "subject": "={{ $json.subject }}", "emailType": "text",
                    "message": "={{ $json.text }}", "options": {"appendAttribution": False}}},
]


def link(*targets): return [[{"node": t, "type": "main", "index": 0} for t in targets]]


connections = {
    "DocSift events": {"main": link("Settings")},
    "Settings": {"main": link("Which event?")},
    "Which event?": {"main": [[{"node": "Sheet row", "type": "main", "index": 0}],
                              [{"node": "Review email", "type": "main", "index": 0}]]},
    "Sheet row": {"main": link("Add to the Sheet")},
    "Review email": {"main": link("Email the reviewer")},
}
workflow = {"name": "DocSift: approved documents to Sheets, reviews by email", "nodes": nodes,
            "connections": connections, "settings": {"executionOrder": "v1", "timezone": "America/New_York"},
            "pinData": {}, "active": False, "meta": {"templateCredsSetupCompleted": False}}
out = Path(__file__).with_name("n8n_docsift.json")
out.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
