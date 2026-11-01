  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (attrs[k] == null || attrs[k] === "") continue;
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function pubmed(pmid, label) {
    return pmid ? el("a", {href: "https://pubmed.ncbi.nlm.nih.gov/" + encodeURIComponent(pmid) + "/", rel: "noopener", text: label || pmid}) : document.createTextNode(label || "");
  }
  function norm(s) { return String(s == null ? "" : s).toLowerCase(); }
  function words(s) { return String(s || "").replace(/_/g, " "); }
  // `onPick`, when given, makes each chip a button that filters by its value.
  function chips(list, glossary, onPick) {
    return (list || []).map(function (p) {
      p = p.trim();
      var cls = (p === "missense_glycine" ? "chip gly" : "chip") + (onPick ? " pick" : "");
      if (!onPick) return el("span", {class: cls, text: words(p), title: glossary[p]});
      var b = el("button", {type: "button", class: cls, text: words(p),
                            title: (glossary[p] ? glossary[p] + ". " : "") + "Click to show only rows with it."});
      b.addEventListener("click", function (e) { e.stopPropagation(); onPick(p); });
      return b;
    });
  }
  function isGlycine(r) { return (r.variant_types || [r.variant_type]).indexOf("missense_glycine") >= 0; }
  function systems(s) { return s ? s.split(",").map(function (x) { return x.trim(); }) : []; }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function short(key) { return String(key || "").replace(/^N[MP]_\d+\.\d+:/, ""); }
  function span(lo, hi) { return lo === hi ? String(lo) : lo + "–" + hi; }
  function rangeHint(lo, hi, one, many) {
    if (lo === hi) return plural(lo, one, many) + " in clinical reports" + (lo ? "" : " (none counted)");
    return lo + " if every report re-describes the same patients, " + hi + " if the papers are independent";
  }
  // Page state lives in the hash (#tab=variants&q=...), so links between pages,
  // back and forward, and shared URLs all land on the same view.
  function hashParams() { return new URLSearchParams(location.hash.replace(/^#/, "")); }
  function dataLink(params) { return "data.html#" + new URLSearchParams(params).toString(); }
