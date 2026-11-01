  var C = D.columns, G = D.glossary;
  var ev = D.evidence, lit = D.literature, va = D.variants;

  // Phenotype filters, per table: clicking a system adds it, and a row must have every one.
  var PHENO = {variants: [], evidence: []};
  function hasAll(r, table) {
    var mine = systems(r.phenotype_systems);
    return PHENO[table].every(function (p) { return mine.indexOf(p) >= 0; });
  }
  function pick(table) {
    return function (p) {
      if (PHENO[table].indexOf(p) < 0) PHENO[table].push(p);
      refilter(table);
    };
  }
  function refilter(table) {
    var bar = document.getElementById(table === "variants" ? "va-filters" : "ev-filters");
    bar.replaceChildren();
    bar.hidden = !PHENO[table].length;
    if (PHENO[table].length) {
      bar.appendChild(el("span", {class: "count", text: "Only rows with"}));
      PHENO[table].forEach(function (p) {
        var x = el("button", {type: "button", class: "chip on", title: "Remove this filter", "aria-label": "Remove " + words(p)},
                   [words(p), el("span", {class: "x", text: "×", "aria-hidden": "true"})]);
        x.addEventListener("click", function () { PHENO[table].splice(PHENO[table].indexOf(p), 1); refilter(table); });
        bar.appendChild(x);
      });
      var clear = el("button", {type: "button", class: "linkish", text: "Clear"});
      clear.addEventListener("click", function () { PHENO[table] = []; refilter(table); });
      bar.appendChild(clear);
    }
    (table === "variants" ? drawVa : drawEv)();
    if (TABS) remember();  // undefined until the tabs are wired, during the first draw
  }

  // ---------------------------------------------------------------- tables
  // A column names the schema fields it shows, so its hint is the published
  // column description, the same text datapackage.json carries.
  function helpFor(table, col) {
    return col.help || (col.fields || []).map(function (f) { return C[table][f] || ""; }).filter(Boolean).join(" ");
  }
  // `name` is the table's key in D.columns; `id` prefixes its elements (va-table, va-pager, va-help).
  function makeTable(name, id, cols, getRows, renderRow, noun) {
    var table = document.getElementById(id + "-table");
    var sortKey = null, sortDir = 1, page = 0, size = 25;
    var thead = table.tHead, tbody = table.tBodies[0];
    var tr = el("tr");
    cols.forEach(function (c) {
      var help = helpFor(name, c);
      var th = el("th", {scope: "col", class: c.cls || "", title: help ? help + " (click to sort)" : "Click to sort"},
                  [el("span", {class: help ? "hint" : "", text: c.label})]);
      th.addEventListener("click", function () {
        sortDir = sortKey === c.key ? -sortDir : 1; sortKey = c.key; page = 0;
        Array.prototype.forEach.call(tr.children, function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
        draw();
      });
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    var helpList = document.getElementById(id + "-help");
    cols.forEach(function (c) {
      var help = helpFor(name, c);
      if (help) { helpList.appendChild(el("dt", {text: c.label})); helpList.appendChild(el("dd", {text: help})); }
    });

    var pager = document.getElementById(id + "-pager");
    var count = el("span", {class: "count", "aria-live": "polite"});
    var prev = el("button", {type: "button", class: "btn", text: "‹ Previous"});
    var info = el("span", {class: "page-of"});
    var next = el("button", {type: "button", class: "btn", text: "Next ›"});
    var per = el("select", {"aria-label": "Rows per page"});
    [25, 50, 100, 250].forEach(function (n) { per.appendChild(el("option", {value: String(n), text: n + " per page"})); });
    per.value = String(size);
    // Previous, the page number and Next stay together as one unit.
    var pagesBox = el("span", {class: "pages"}, [prev, info, next]);
    pager.append(count, pagesBox, per);
    prev.addEventListener("click", function () { page--; draw(); table.scrollIntoView({block: "nearest"}); });
    next.addEventListener("click", function () { page++; draw(); table.scrollIntoView({block: "nearest"}); });
    per.addEventListener("change", function () { size = +per.value; page = 0; draw(); });

    function draw() {
      var rows = getRows();
      if (sortKey) {
        var col = cols.filter(function (c) { return c.key === sortKey; })[0];
        rows = rows.slice().sort(function (a, b) {
          var x = col.value(a), y = col.value(b);
          if (typeof x === "number" && typeof y === "number") return (x - y) * sortDir;
          return String(x).localeCompare(String(y)) * sortDir;
        });
      }
      var pages = Math.max(1, Math.ceil(rows.length / size));
      page = Math.min(Math.max(page, 0), pages - 1);
      var frag = document.createDocumentFragment();
      rows.slice(page * size, page * size + size).forEach(function (r) { renderRow(r, frag); });
      tbody.replaceChildren(frag);
      if (!rows.length) tbody.appendChild(el("tr", null, [el("td", {colspan: String(cols.length), class: "count", text: "Nothing matches."})]));
      count.textContent = rows.length ? (page * size + 1) + "–" + Math.min(rows.length, page * size + size) + " of " + rows.length + " " + noun : "0 " + noun;
      info.textContent = "Page " + (page + 1) + " of " + pages;
      // On the first page there is no Previous and on the last no Next; hidden, not
      // removed, so the page number does not shift as you page.
      prev.style.visibility = page === 0 ? "hidden" : ""; next.style.visibility = page >= pages - 1 ? "hidden" : "";
      // Paging controls only when there is more than the smallest page to page through.
      [pagesBox, per].forEach(function (n) { n.hidden = rows.length <= 25; });
    }
    return function () { page = 0; draw(); };
  }
  function expandable(tr, cols, buildDetail, frag) {
    tr.setAttribute("tabindex", "0"); tr.setAttribute("aria-expanded", "false");
    tr.setAttribute("title", "Click for details");
    function toggle() {
      var next = tr.nextSibling;
      if (next && next.classList && next.classList.contains("detail")) { next.remove(); tr.setAttribute("aria-expanded", "false"); return; }
      tr.after(el("tr", {class: "detail"}, [el("td", {colspan: String(cols.length)}, [el("div", {class: "detail-inner"}, buildDetail())])]));
      tr.setAttribute("aria-expanded", "true");
    }
    tr.addEventListener("click", function (e) { if (e.target.tagName !== "A") toggle(); });
    tr.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    frag.appendChild(tr);
  }
  function details() {
    var dl = el("dl");
    return {dl: dl, item: function (label, value, title) {
      if (value === "" || value == null) return;
      dl.appendChild(el("dt", {text: label, title: title}));
      dl.appendChild(el("dd", null, [typeof value === "string" ? value : value]));
    }};
  }

  // ---------------------------------------------------------------- variants
  document.getElementById("va-note").textContent = D.variants_note;
  var vaQ = document.getElementById("va-q"), vaGene = document.getElementById("va-gene");
  var vaCols = [
    {key: "gene", label: "Gene", fields: ["gene"], value: function (r) { return r.gene; }},
    {key: "variant", label: "Variant", fields: ["variant_id"], value: function (r) { return r.protein_position == null ? 1e9 : r.protein_position; }},
    {key: "papers", label: "Papers", cls: "num", fields: ["n_papers"], value: function (r) { return r.n_papers; }},
    {key: "probands", label: "Probands", cls: "num", fields: ["probands_min", "probands_max"], value: function (r) { return r.probands_max; }},
    {key: "families", label: "Families", cls: "num hide-sm", help: "Kindreds, as a range like probands: fewest if every report re-describes the same families, most if the papers are independent.", value: function (r) { return r.families_max; }},
    {key: "screened", label: "Screened", cls: "num hide-sm", fields: ["screened_carriers"], value: function (r) { return r.screened_carriers; }},
    {key: "denovo", label: "De novo", cls: "num hide-sm", help: "Papers reporting at least one de novo case: confirmed with both parents tested, or assumed without parental testing described (shown in brackets). Counted in papers, not people, so overlap between papers cannot inflate it.", value: function (r) { return r.papers_de_novo_confirmed + r.papers_de_novo_assumed; }},
    {key: "inherited", label: "Inherited", cls: "num hide-sm", fields: ["papers_inherited"], value: function (r) { return r.papers_inherited; }},
    {key: "pheno", label: "Phenotypes", cls: "hide-sm", fields: ["phenotype_systems"], value: function (r) { return r.phenotype_systems; }},
    {key: "years", label: "Reported", cls: "hide-sm", help: "Years of the earliest and latest report.", value: function (r) { return r.first_year; }}
  ];
  function vaRow(r, frag) {
    var dn = r.papers_de_novo_confirmed + r.papers_de_novo_assumed;
    var tr = el("tr", {class: "rec"}, [
      el("td", {class: "gene", text: r.gene}),
      el("td", {class: "variant", text: short(r.variant_id),
                title: r.variant_id + (r.grouped_by === "protein" && r.coding_keys.length ? "\nfrom " + r.coding_keys.join(", ") : "")}),
      el("td", {class: "num", text: String(r.n_papers),
                title: plural(r.n_papers, "paper", "papers") + (r.pointer_pmids.length ? ", " + plural(r.pointer_pmids.length, "review", "reviews") + " not counted" : "")}),
      el("td", {class: "num", text: span(r.probands_min, r.probands_max), title: rangeHint(r.probands_min, r.probands_max, "proband", "probands")}),
      el("td", {class: "num hide-sm", text: span(r.families_min, r.families_max), title: rangeHint(r.families_min, r.families_max, "family", "families")}),
      el("td", {class: "num hide-sm", text: r.screened_carriers ? String(r.screened_carriers) : "—",
                title: r.screened_carriers ? "Found by screening at least 1,000 people, not counted as probands" : "None from population screens"}),
      el("td", {class: "num hide-sm", text: String(dn) + (r.papers_de_novo_assumed ? " (" + r.papers_de_novo_assumed + ")" : ""),
                title: plural(r.papers_de_novo_confirmed, "paper", "papers") + " confirmed de novo, " + r.papers_de_novo_assumed + " assumed"}),
      el("td", {class: "num hide-sm", text: String(r.papers_inherited), title: plural(r.papers_inherited, "paper reports", "papers report") + " an inherited case"}),
      el("td", {class: "hide-sm"}, chips(systems(r.phenotype_systems), G.phenotype_systems, pick("variants"))),
      el("td", {class: "hide-sm", text: r.first_year === r.last_year ? r.first_year : r.first_year + "–" + r.last_year})
    ]);
    expandable(tr, vaCols, function () {
      // The same shape as an evidence row's detail: the variant first, then its papers.
      var v = details(), V = C.variants;
      v.item("Normalized", r.variant_id, V.variant_id);
      v.item("Coding changes", r.coding_keys.join(", "), V.coding_keys);
      v.item("GRCh38", r.grch38.join(", "), V.grch38);
      v.item("Type", el("span", null, chips(r.variant_types, G.variant_types)), V.variant_types);
      v.item("Normalization flags", r.normalization_flags, V.normalization_flags);
      var d = details();
      d.item("Reported before", r.records_prior_report ? plural(r.records_prior_report, "record says", "records say") + " the family was reported before" : "", V.records_prior_report);
      d.item("Functional work", r.papers_functional ? plural(r.papers_functional, "paper", "papers") : "", V.papers_functional);
      var links = el("span");
      r.pmids.forEach(function (p, i) { if (i) links.appendChild(document.createTextNode(", ")); links.appendChild(pubmed(p)); });
      d.item("PMIDs", links, C.variants.pmids);
      d.item("Reviews (not counted)", r.pointer_pmids.join(", "), C.variants.pointer_pmids);
      var go = el("button", {type: "button", class: "btn", text: "Show the " + plural(r.n_records, "record", "records") + " behind this row"});
      go.addEventListener("click", function () {
        evQ.value = r.variant_id; evGene.value = ""; evVerdict.value = ""; show("evidence"); drawEv();
        document.getElementById("tabs").scrollIntoView();
      });
      return [el("h3", {text: "Variant"}), v.dl, el("h3", {text: "Papers"}), d.dl, go];
    }, frag);
  }
  var drawVa = makeTable("variants", "va", vaCols, function () {
    var q = norm(vaQ.value), g = vaGene.value;
    return va.filter(function (r) {
      if (g && r.gene !== g) return false;
      if (!hasAll(r, "variants")) return false;
      if (!q) return true;
      return norm([r.variant_id, r.coding_keys.join(" "), r.grch38.join(" "), r.pmids.join(" "), r.phenotype_systems, r.variant_types.join(" ")].join(" ")).indexOf(q) >= 0;
    });
  }, vaRow, "variants");
  [vaQ, vaGene].forEach(function (n) { n.addEventListener("input", drawVa); });

  // ---------------------------------------------------------------- evidence
  var evQ = document.getElementById("ev-q"), evGene = document.getElementById("ev-gene"), evVerdict = document.getElementById("ev-verdict");
  if (D.include_all) {
    evVerdict.hidden = false;
    Object.keys(D.verdicts).sort().forEach(function (v) { evVerdict.appendChild(el("option", {value: v, text: v + " (" + D.verdicts[v] + ")"})); });
  }
  // "Florquin M, et al. 2026": the author part of the prepared citation and the year;
  // the full citation is on hover and in the details.
  function shortCite(r) {
    var author = (r.citation || "").split(". ")[0];
    return author ? author.replace(/et al$/, "et al.") + " " + (r.year || "") : r.pmid;
  }
  // The normalized form shown in the table: the protein change, else the coding change.
  function evVariant(r) { return r.variant_key ? short(r.protein_key || r.variant_key) : r.as_written; }
  function inheritance(r) {
    var parts = [];
    if (r.inh_de_novo_confirmed) parts.push(r.inh_de_novo_confirmed + " de novo");
    if (r.inh_de_novo_assumed) parts.push(r.inh_de_novo_assumed + " de novo (assumed)");
    if (r.inh_inherited) parts.push(r.inh_inherited + " inherited");
    if (r.inh_unknown) parts.push(r.inh_unknown + " unknown");
    return parts.join(", ");
  }
  var evCols = [
    {key: "gene", label: "Gene", fields: ["gene"], value: function (r) { return r.gene; }},
    {key: "variant", label: "Variant", help: "The variant normalized by Ensembl VEP on the reference sequence, as the Variants tab groups it. The paper's own wording is in the row's details; a record that could not be normalized shows it here in italics.", value: evVariant},
    {key: "type", label: "Type", cls: "hide-sm", fields: ["variant_type"], value: function (r) { return r.variant_type; }},
    {key: "probands", label: "Probands", cls: "num", fields: ["n_probands"], value: function (r) { return r.n_probands; }},
    {key: "families", label: "Families", cls: "num hide-sm", fields: ["n_families"], value: function (r) { return r.n_families; }},
    {key: "tested", label: "Tested", cls: "num hide-sm", fields: ["n_tested"], value: function (r) { return r.n_tested; }},
    {key: "inh", label: "Inheritance", cls: "hide-sm", help: "How the probands' variants arose: de novo with both parents tested, de novo assumed without parental testing described, inherited from a parent, or not reported.", value: inheritance},
    {key: "pheno", label: "Phenotypes", cls: "hide-sm", fields: ["phenotype_systems"], value: function (r) { return r.phenotype_systems; }},
    {key: "paper", label: "Paper", fields: ["citation"], value: function (r) { return r.citation; }}
  ];
  if (D.include_all) evCols.push({key: "verdict", label: "Review", fields: ["reviewer_verdict"], value: function (r) { return r.reviewer_verdict; }});
  function evRow(r, frag) {
    var cells = [
      el("td", {class: "gene", text: r.gene}),
      r.variant_key
        ? el("td", {class: "variant", text: evVariant(r), title: "As written in the paper: " + r.as_written})
        : el("td", {class: "variant none", text: r.as_written.length > 40 ? r.as_written.slice(0, 38).trim() + "…" : r.as_written,
                    title: "Not normalized" + (r.normalization_flags ? " (" + r.normalization_flags + ")" : "") + "; as written in the paper: " + r.as_written}),
      el("td", {class: "hide-sm"}, chips([r.variant_type], G.variant_types)),
      el("td", {class: "num", text: String(r.n_probands), title: "Unrelated index cases in this paper"}),
      el("td", {class: "num hide-sm", text: String(r.n_families), title: r.n_families ? "" : "0: carriers came from screening, or no kindreds were described"}),
      el("td", {class: "num hide-sm", text: r.n_tested ? String(r.n_tested) : "—", title: r.n_tested ? "People screened or sequenced in the study" : "Not stated"}),
      el("td", {class: "hide-sm", text: inheritance(r)}),
      el("td", {class: "hide-sm"}, chips(systems(r.phenotype_systems), G.phenotype_systems, pick("evidence"))),
      el("td", {class: "nowrap", title: r.citation}, [pubmed(r.pmid, shortCite(r))])
    ];
    if (D.include_all) {
      var cls = r.reviewer_verdict === "upheld" ? "v-upheld" : r.reviewer_verdict === "disputed" ? "v-disputed" : "v-other";
      cells.push(el("td", {class: cls, text: r.reviewer_verdict + (r.review_flags ? "; " + r.review_flags : ""), title: C.evidence.review_flags}));
    }
    expandable(el("tr", {class: "rec"}, cells), evCols, function () {
      var E = C.evidence;
      // The variant as the paper wrote it beside what normalization made of it.
      var v = details();
      // The paper's HGVS, where the wording does not already contain it.
      var paper = [r.hgvs_c, r.hgvs_p].filter(function (x) {
        return x && r.as_written.indexOf(x.replace(/^p\.\((.*)\)$/, "p.$1")) < 0 && r.as_written.indexOf(x) < 0;
      });
      v.item("As written", r.as_written + (paper.length ? " (" + paper.join(", ") + ")" : "") +
             (r.transcript_if_stated ? " on " + r.transcript_if_stated : ""), E.as_written);
      v.item("Normalized", r.variant_key ? [r.protein_key, r.key_level === "coding" && r.variant_key !== r.protein_key ? r.variant_key : ""].filter(Boolean).join(", ")
                                          : "Not normalized", E.variant_key);
      v.item("GRCh38", r.grch38, E.grch38);
      v.item("Normalization flags", r.normalization_flags, E.normalization_flags);
      var d = details();
      d.item("Phenotype", r.phenotype_details, E.phenotype_details);
      d.item("Carriers", r.n_total_carriers != null ? String(r.n_total_carriers) : "", E.n_total_carriers);
      d.item("Functional work", r.functional_evidence ? r.functional_evidence + " (summary, not a quote)" : "", E.functional_evidence);
      d.item("Authors' classification", r.reported_classification, E.reported_classification);
      d.item("Reported before", r.same_family_as_prior_report ? (r.prior_report_reason || "yes") : "", E.same_family_as_prior_report);
      d.item("Paper", pubmed(r.pmid, r.citation || r.pmid), E.citation);
      d.item("Paper type", words(r.paper_type), E.paper_type);
      d.item("Reviewer's problem", r.reviewer_problem, E.reviewer_problem);
      d.item("Extracted", r.extracted_by + ", prompt " + r.prompt_version + ", " + r.extracted_at.slice(0, 10), E.extracted_by);
      var quotes = (r.evidence_quotes || []).map(function (q) { return el("blockquote", {text: q}); });
      return [el("h3", {text: "Variant"}), v.dl, el("h3", {text: "In the paper's words", title: E.evidence_quotes})]
        .concat(quotes, [el("h3", {text: "What was extracted"}), d.dl]);
    }, frag);
  }
  var drawEv = makeTable("evidence", "ev", evCols, function () {
    var q = norm(evQ.value), g = evGene.value, v = evVerdict.value;
    return ev.filter(function (r) {
      if (g && r.gene !== g) return false;
      if (!hasAll(r, "evidence")) return false;
      if (v && r.reviewer_verdict !== v) return false;
      if (!q) return true;
      return norm([r.as_written, r.hgvs_c, r.hgvs_p, r.variant_key, r.protein_key, r.citation, r.pmid, r.phenotype_systems, r.phenotype_details, r.variant_type].join(" ")).indexOf(q) >= 0;
    });
  }, evRow, "records");
  [evQ, evGene, evVerdict].forEach(function (n) { n.addEventListener("input", drawEv); });

  // ---------------------------------------------------------------- literature
  var litQ = document.getElementById("lit-q"), litStatus = document.getElementById("lit-status");
  var litCols = [
    {key: "date", label: "Published", fields: ["first_pub_date"], value: function (r) { return r.first_pub_date; }},
    {key: "title", label: "Title", fields: ["title"], help: C.literature.title + ", with the triage gate's one-line reason beneath (machine-written).", value: function (r) { return r.title; }},
    {key: "journal", label: "Journal", cls: "hide-sm", fields: ["journal"], value: function (r) { return r.journal; }},
    {key: "status", label: "Status", help: "What became of the paper; hover a status for its meaning.", value: function (r) { return r.status; }},
    {key: "records", label: "Records", cls: "num hide-sm", fields: ["n_records"], value: function (r) { return r.n_records; }}
  ];
  var STATUS = {extracted: "read", kept_not_read: "no open full text", kept_pending: "pending", not_relevant: "not relevant", not_triaged: "not assessed"};
  function litRow(r, frag) {
    frag.appendChild(el("tr", null, [
      el("td", {class: "nowrap", text: r.first_pub_date}),
      el("td", null, [pubmed(r.pmid, r.title || r.pmid), r.gate_reason ? el("div", {class: "count", text: r.gate_reason}) : null]),
      el("td", {class: "hide-sm", text: r.journal}),
      el("td", {text: STATUS[r.status] || r.status.replace("dropped:", "dropped: ").replace(/_/g, " "), title: D.statuses[r.status]}),
      el("td", {class: "num hide-sm", text: String(r.n_records), title: "Records from this paper in this release"})
    ]));
  }
  var drawLit = makeTable("literature", "lit", litCols, function () {
    var q = norm(litQ.value), s = litStatus.value;
    return lit.filter(function (r) {
      if (s === "kept" && ["extracted", "kept_not_read", "kept_pending"].indexOf(r.status) < 0) return false;
      if (s && s !== "kept" && r.status !== s) return false;
      if (!q) return true;
      return norm([r.title, r.journal, r.pmid, r.gate_reason].join(" ")).indexOf(q) >= 0;
    });
  }, litRow, "papers");
  [litQ, litStatus].forEach(function (n) { n.addEventListener("input", drawLit); });
  Object.keys(D.statuses).forEach(function (s) {
    var h = document.getElementById("lit-help");
    h.appendChild(el("dt", {text: STATUS[s] || s.replace("dropped:", "dropped: ").replace(/_/g, " ")}));
    h.appendChild(el("dd", {text: D.statuses[s]}));
  });

  // ---------------------------------------------------------------- tabs and the URL
  var TABS = {variants: [vaQ, null, drawVa], evidence: [evQ, null, drawEv], literature: [litQ, litStatus, drawLit]};
  var current = "variants";
  function remember() {
    var p = {tab: current}, t = TABS[current];
    if (t[0].value) p.q = t[0].value;
    if (t[1] && t[1].value !== "kept") p.status = t[1].value;
    if (PHENO[current] && PHENO[current].length) p.pheno = PHENO[current].join(",");
    history.replaceState(null, "", "#" + new URLSearchParams(p).toString());
  }
  function show(which) {
    current = which;
    Object.keys(TABS).forEach(function (k) {
      document.getElementById("pane-" + k).hidden = k !== which;
      document.getElementById("tab-" + k).setAttribute("aria-selected", String(k === which));
    });
    remember();
  }
  function fromHash() {
    var p = hashParams(), tab = TABS[p.get("tab")] ? p.get("tab") : "variants", t = TABS[tab];
    if (p.has("q")) t[0].value = p.get("q");
    if (t[1] && p.has("status")) t[1].value = p.get("status");
    if (PHENO[tab]) { PHENO[tab] = p.get("pheno") ? p.get("pheno").split(",") : []; show(tab); refilter(tab); return; }
    show(tab); t[2]();
  }
  Object.keys(TABS).forEach(function (k) {
    document.getElementById("tab-" + k).addEventListener("click", function () { show(k); });
    TABS[k].filter(function (n) { return n && n.tagName; }).forEach(function (n) { n.addEventListener("input", remember); });
  });
  window.addEventListener("hashchange", fromHash);

  drawVa(); drawEv(); drawLit();
  fromHash();
