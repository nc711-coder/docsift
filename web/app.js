/* DocSift web page: upload or pick a sample, then review the extracted fields beside the document.
   No framework, no build step. Everything the server returns is inserted as text, never as HTML. */
(function () {
  "use strict";
  var app = document.getElementById("app");
  var MINE = "docsift-mine";
  var SAMPLE_PICKS = [   // a spread of the test set: types, scans, photos, tricky cases
    ["inv-01", "Invoice", "clean PDF"], ["inv-16", "Invoice", "phone photo"], ["inv-06", "Invoice", "2 pages"],
    ["inv-05", "Invoice", "scan"], ["rcp-02", "Restaurant receipt", "with tip"], ["rcp-12", "Receipt", "phone photo"],
    ["rcp-08", "Fuel receipt", "tax included"], ["w9-01", "W-9", "clean PDF"], ["w9-14", "W-9", "phone photo"],
    ["w9-16", "W-9", "scan"]];
  var TYPE_NAMES = { invoice: "Invoice", receipt: "Receipt", w9: "W-9", other: "Not supported" };
  var STATUS = { approved: "Approved", needs_review: "Needs review", failed: "Couldn't extract" };

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") n.textContent = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else if (k === "value") n.value = v;
      else if (k === "checked") n.checked = !!v;
      else n.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function show(id) {
    app.textContent = "";
    app.appendChild(document.getElementById(id).content.cloneNode(true));
    app.focus({ preventScroll: true });
  }
  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { t.hidden = true; }, 4200);
  }
  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body instanceof FormData) opts.body = body;
    else if (body) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong (HTTP " + r.status + ").");
        return data;
      });
    });
  }
  function mine() { try { return JSON.parse(localStorage.getItem(MINE) || "[]"); } catch (e) { return []; } }
  function remember(doc) {
    var list = mine().filter(function (d) { return d.id !== doc.id; });
    list.unshift({ id: doc.id, name: doc.filename, type: doc.type, status: doc.status, at: Date.now() });
    try { localStorage.setItem(MINE, JSON.stringify(list.slice(0, 30))); } catch (e) {}
  }
  function ago(ms) {
    var m = Math.round((Date.now() - ms) / 60000);
    return m < 1 ? "just now" : m < 60 ? m + " min ago" : Math.round(m / 60) + " h ago";
  }

  // ------------------------------------------------------------------ home
  function home() {
    show("home");
    var input = document.getElementById("file"), drop = document.getElementById("drop");
    input.addEventListener("change", function () { if (input.files[0]) upload(input.files[0]); });
    ["dragenter", "dragover"].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (e) {
      drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (ev) { var f = ev.dataTransfer.files[0]; if (f) upload(f); });

    var grid = document.getElementById("samples");
    SAMPLE_PICKS.forEach(function (s) {
      grid.appendChild(el("button", { type: "button", class: "sample", onclick: function () { runSample(s[0]); } }, [
        el("b", { text: s[1] }),
        el("span", { class: "chip " + (s[2].indexOf("photo") >= 0 ? "photo" : s[2] === "scan" ? "scan" : ""), text: s[2] }),
        el("small", { text: s[0] })]));
    });
    var list = mine();
    if (list.length) {
      document.getElementById("mine-wrap").hidden = false;
      var ul = document.getElementById("mine");
      list.forEach(function (d) {
        ul.appendChild(el("li", {}, [el("a", { href: "#/doc/" + d.id }, [
          el("span", { class: "badge " + d.status, text: STATUS[d.status] || d.status }),
          el("span", { class: "name", text: (TYPE_NAMES[d.type] || "") + " · " + d.name }),
          el("small", { class: "muted", text: ago(d.at) })])]));
      });
    }
  }

  function busy(name) {
    show("busy");
    document.getElementById("busy-name").textContent = name;
    var steps = document.querySelectorAll("#steps li"), i = 0;
    function tick() {
      steps.forEach(function (li, j) { li.className = j < i ? "done" : j === i ? "now" : ""; });
      if (i < steps.length - 1) { i++; busy.timer = setTimeout(tick, i === 1 ? 2600 : 1800); }
    }
    tick();
  }
  function done(doc) {
    clearTimeout(busy.timer);
    remember(doc);
    location.hash = "#/doc/" + doc.id;
  }
  function failed(e) {
    clearTimeout(busy.timer);
    home();
    toast(e.message);
  }
  function upload(file) {
    if (file.size > 5 * 1024 * 1024) { toast("That file is over 5 MB. Please upload a smaller one."); return; }
    busy(file.name);
    var fd = new FormData();
    fd.append("file", file);
    api("POST", "/api/documents", fd).then(done, failed);
  }
  function runSample(id) {
    busy("sample " + id);
    api("POST", "/api/samples/" + id).then(done, failed);
  }

  // ------------------------------------------------------------------ review
  var current = null;

  function review(id) {
    show("review");
    api("GET", "/api/documents/" + id).then(function (doc) {
      current = doc;
      remember(doc);
      renderHead(doc);
      renderDoc(doc);
      var person = doc.approved_by === "person";       // a person checked the values: no dots, no doubts
      renderFields(doc, doc.fields || {}, person ? {} : doc.confidence, person ? [] : doc.issues);
    }, function (e) {
      app.textContent = "";
      app.appendChild(el("div", { class: "card" }, [el("p", { class: "error", text: e.message }), el("a", { href: "#/", text: "← Back" })]));
    });
  }

  function renderHead(doc) {
    document.getElementById("r-title").textContent = (TYPE_NAMES[doc.type] || "Document") + " · " + doc.filename;
    var badge = document.getElementById("r-badge");
    badge.className = "badge " + doc.status;
    badge.textContent = doc.status === "approved" ? (doc.approved_by === "person" ? "Approved by you" : "Approved automatically")
      : STATUS[doc.status];
    var s = doc.seconds || {};
    document.getElementById("r-meta").textContent = [
      s.total ? "processed in " + s.total.toFixed(1) + " s" : null,
      doc.classified_by ? "type found by " + (doc.classified_by === "rules" ? "rules" : "the AI") : null
    ].filter(Boolean).join(" · ");
    var banner = document.getElementById("r-banner");
    banner.textContent = "";
    if (doc.status === "approved") {
      banner.className = "banner ok";
      banner.appendChild(el("strong", { text: doc.approved_by === "person" ? "Approved and sent to the Google Sheet." :
        "Every check passed: approved automatically and sent to the Google Sheet." }));
    } else if (doc.status === "failed" || doc.type === "other") {
      banner.className = "banner fail";
      banner.appendChild(el("strong", { text: doc.type === "other" ? "This doesn't look like an invoice, receipt or W-9." :
        "The fields couldn't be extracted." }));
      if (doc.reasons.length) banner.appendChild(el("p", { text: doc.reasons[0] }));
    } else {
      banner.className = "banner warn";
      banner.appendChild(el("strong", { text: "Needs a quick look before it's approved:" }));
      banner.appendChild(el("ul", {}, doc.reasons.map(function (r) { return el("li", { text: plain(r) }); })));
    }
  }

  function plain(reason) {   // "vendor_address: 'x' isn't on the page" -> "Vendor address: 'x' isn't on the page"
    var m = reason.match(/^([a-z_0-9]+)(\[\d+\])?: (.*)$/);
    if (!m || !current) return reason;
    var f = (current.form || []).filter(function (x) { return x.name === m[1]; })[0];
    return (f ? f.label : m[1]) + (m[2] ? " " + m[2].replace(/\[|\]/g, "#") : "") + ": " + m[3];
  }

  function renderDoc(doc) {
    var box = document.getElementById("r-doc");
    var src = doc.sample ? "/api/samples/" + doc.sample + "/file" : doc.has_file ? "/api/documents/" + doc.id + "/file" : null;
    if (!src) {
      box.appendChild(el("p", { class: "muted", style: "padding:16px", text: "The original file is no longer kept (originals are deleted after 24 hours)." }));
    } else if (doc.content_type === "application/pdf") {
      box.appendChild(el("iframe", { src: src + "#view=FitH&toolbar=0", title: "The uploaded document" }));
    } else {
      box.appendChild(el("img", { src: src, alt: "The uploaded document" }));
    }
    var r = doc.reading || {};
    document.getElementById("r-read").textContent = r.method === "text" ? "Read from the PDF's own text (exact)." :
      r.method ? "Read with OCR, confidence " + Math.round(r.ocr_confidence) + "/100" +
        (r.notes && r.notes.length ? " · " + r.notes.join(" · ") : "") + "." : "";
  }

  // per-field problems from the checks: field -> [{problem, severity}]
  function problemsBy(issues) {
    var out = {};
    (issues || []).forEach(function (i) {
      var k = i.field.split("[")[0];
      (out[k] = out[k] || []).push(i);
    });
    return out;
  }

  function renderFields(doc, fields, confidence, issues) {
    var box = document.getElementById("r-fields");
    box.textContent = "";
    if (!doc.form.length) { box.appendChild(el("p", { class: "muted", text: "Nothing to review for this document." })); return; }
    var probs = problemsBy(issues);
    var groups = { invoice: [["Who", ["vendor_name", "vendor_address", "bill_to_name", "bill_to_address"]],
                             ["Invoice", ["invoice_number", "invoice_date", "due_date", "po_number", "payment_terms", "currency"]],
                             ["Amounts", ["subtotal", "discount", "tax", "shipping", "total"]], ["Line items", ["line_items"]]],
                   receipt: [["Where and when", ["merchant_name", "merchant_address", "date", "time", "category"]],
                             ["Amounts", ["subtotal", "tax", "tax_included", "tip", "total"]],
                             ["Payment", ["payment_method", "card_last4"]], ["Items", ["items"]]],
                   w9: [["Taxpayer", ["name", "business_name", "tax_classification", "llc_tax_code"]],
                        ["Address", ["address", "city_state_zip"]], ["Tax ID", ["tin_type", "tin_last4"]],
                        ["Signature", ["signed", "date_signed"]]] }[doc.type] || [];
    var spec = {};
    doc.form.forEach(function (f) { spec[f.name] = f; });
    groups.forEach(function (g) {
      var fs = el("fieldset", {}, [el("legend", { text: g[0] })]);
      g[1].forEach(function (name) {
        var f = spec[name];
        if (!f) return;
        fs.appendChild(f.kind === "rows" ? rowsTable(f, fields[name] || [], probs[name]) :
          fieldRow(f, fields[name], confidence[name], probs[name]));
      });
      box.appendChild(fs);
    });
    var editable = doc.status !== "failed" && doc.type !== "other";
    document.getElementById("r-approve").hidden = !editable;
    document.getElementById("r-check").hidden = !editable;
    document.getElementById("r-approve").textContent = doc.status === "approved" ? "Save changes" : "Approve";
  }

  function fieldRow(f, value, conf, probs) {
    var id = "f-" + f.name, input;
    if (f.kind === "choice") {
      input = el("select", { id: id, name: f.name }, [el("option", { value: "", text: "—" })].concat(
        f.options.map(function (o) { return el("option", { value: o, text: o.replace(/_/g, " ") }); })));
      input.value = value == null ? "" : value;
    } else if (f.kind === "bool") {
      input = el("input", { id: id, name: f.name, type: "checkbox", checked: !!value });
    } else {
      input = el("input", { id: id, name: f.name, type: f.kind === "date" ? "date" : "text",
                            inputmode: f.kind === "money" ? "decimal" : null,
                            value: value == null ? "" : f.kind === "money" ? Number(value).toFixed(2) : value });
    }
    input.dataset.kind = f.kind;
    var errs = (probs || []).filter(function (p) { return p.severity === "error"; });
    var warns = (probs || []).filter(function (p) { return p.severity !== "error"; });
    var level = errs.length ? "bad" : (warns.length || (conf != null && conf < 0.9)) ? "mid" : conf == null ? "" : "good";
    var row = el("div", { class: "row " + (f.kind === "money" ? "money " : "") + (errs.length ? "doubt" : warns.length ? "rough" : "") }, [
      el("label", { for: id }, [f.label, f.required ? el("span", { class: "req", text: " *" }) : null]),
      input,
      el("span", { class: "dot " + level, title: conf == null ? "" : "confidence " + Math.round(conf * 100) + "%" })]);
    (errs.concat(warns)).forEach(function (p) { row.appendChild(el("div", { class: "why", text: p.problem })); });
    return row;
  }

  function rowsTable(f, rows, probs) {
    var cols = f.options;     // description, quantity, unit_price, amount
    var heads = { description: "Description", quantity: "Qty", unit_price: "Unit price", amount: "Amount" };
    var tbody = el("tbody");
    function addRow(r) {
      var tr = el("tr");
      cols.forEach(function (c) {
        var v = r[c];
        tr.appendChild(el("td", { class: c === "description" ? "" : "num" }, [el("input", {
          "data-col": c, "aria-label": heads[c], inputmode: c === "description" ? null : "decimal",
          value: v == null ? "" : (c === "amount" || c === "unit_price") ? Number(v).toFixed(2) : v })]));
      });
      tr.appendChild(el("td", {}, [el("button", { type: "button", class: "rm", "aria-label": "Remove this row", text: "×",
        onclick: function () { tr.remove(); } })]));
      tbody.appendChild(tr);
    }
    rows.forEach(addRow);
    var table = el("table", { class: "items", "data-name": f.name }, [
      el("thead", {}, [el("tr", {}, cols.map(function (c) { return el("th", { text: heads[c] || c }); }).concat([el("th")]))]), tbody]);
    var wrap = el("div", { class: "items-wrap" }, [table]);
    var out = el("div", {}, [wrap, el("button", { type: "button", class: "btn ghost add-row", text: "+ Add a row",
      onclick: function () { addRow({}); } })]);
    (probs || []).forEach(function (p) { out.appendChild(el("div", { class: "why", style: "color:var(--mid);font-size:12.5px", text: p.field + ": " + p.problem })); });
    return out;
  }

  function collect() {
    var form = document.getElementById("r-form"), out = {};
    form.querySelectorAll(".row input, .row select").forEach(function (i) {
      var k = i.dataset.kind, v;
      if (k === "bool") v = i.checked;
      else if (i.value.trim() === "") v = null;
      else if (k === "money") v = i.value.replace(/[$,\s]/g, "");
      else v = i.value.trim();
      out[i.name] = v;
    });
    form.querySelectorAll("table.items").forEach(function (t) {
      var rows = [];
      t.querySelectorAll("tbody tr").forEach(function (tr) {
        var r = {}, any = false;
        tr.querySelectorAll("input").forEach(function (i) {
          var v = i.value.trim();
          if (v) any = true;
          r[i.dataset.col] = v === "" ? null : i.dataset.col === "description" ? v : v.replace(/[$,\s]/g, "");
        });
        if (any) rows.push(r);
      });
      out[t.dataset.name] = rows;
    });
    return out;
  }

  function wireReview() {
    var form = document.getElementById("r-form");
    if (!form || form.wired) return;
    form.wired = true;
    document.getElementById("r-check").addEventListener("click", function () {
      var b = this; b.disabled = true;
      api("POST", "/api/documents/" + current.id + "/check", { fields: collect() }).then(function (rep) {
        b.disabled = false;
        if (!rep.valid) { toast(rep.problems[0]); return; }
        renderFields(current, rep.fields, rep.confidence, rep.issues);
        toast(rep.reasons.length ? rep.reasons.length + " thing(s) still look doubtful. You can approve anyway." : "Everything checks out.");
      }, function (e) { b.disabled = false; toast(e.message); });
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var b = document.getElementById("r-approve"), fields = collect();
      b.disabled = true;
      // First click with doubts left: show them and ask for a second, deliberate click.
      var sure = b.dataset.anyway === "1";
      (sure ? Promise.resolve(null) : api("POST", "/api/documents/" + current.id + "/check", { fields: fields }))
        .then(function (rep) {
          if (rep && !rep.valid) { b.disabled = false; toast(rep.problems[0]); return; }
          var errs = rep ? rep.issues.filter(function (i) { return i.severity === "error" && i.kind === "doubt"; }) : [];
          if (errs.length) {
            renderFields(current, rep.fields, rep.confidence, rep.issues);
            b.disabled = false; b.dataset.anyway = "1";
            b.textContent = "Approve anyway";
            toast(errs.length + " value(s) still look doubtful (in red). Fix them, or click \"Approve anyway\".");
            return;
          }
          return api("POST", "/api/documents/" + current.id + "/approve", { fields: fields }).then(function (doc) {
            b.disabled = false; delete b.dataset.anyway;
            current = doc; remember(doc);
            renderHead(doc);
            renderFields(doc, doc.fields, {}, []);
            toast("Approved. It's on its way to the Google Sheet.");
            window.scrollTo({ top: 0, behavior: "smooth" });
          });
        }).catch(function (e) { b.disabled = false; toast(e.message); });
    });
    form.addEventListener("input", function () {      // an edit after "Approve anyway": check again first
      var b = document.getElementById("r-approve");
      if (b.dataset.anyway) { delete b.dataset.anyway; b.textContent = current.status === "approved" ? "Save changes" : "Approve"; }
    });
  }

  // ------------------------------------------------------------------ routing
  function route() {
    var m = location.hash.match(/^#\/doc\/([0-9a-f-]{36})$/);
    if (m) { review(m[1]); wireReview(); } else home();
  }
  window.addEventListener("hashchange", route);
  route();
})();
