  // ---------------------------------------------------------------- summary
  var va = D.variants, N = D.counts;
  document.getElementById("summary").append(
    plural(N.variants, "variant", "variants") + " from " + plural(N.papers, "paper", "papers") +
    ", out of " + N.considered.toLocaleString("en") + " papers considered. " +
    plural(N.watch, "paper", "papers") + " judged relevant can't be read here yet; they are on ",
    el("a", {href: dataLink({tab: "literature", status: "kept_not_read"}), text: "the watch list"}), ".");

  // ---------------------------------------------------------------- protein map
  // Drawn at the container's own pixel width, so labels stay legible on a phone.
  var SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, kids) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    (kids || []).forEach(function (c) { n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  var mapSvg = document.getElementById("map-svg"), lastW = 0;
  var genes = Object.keys(D.proteins);
  var unplaced = va.filter(function (r) { return r.protein_position == null; }).length;
  document.getElementById("map-note").textContent = "Taller marks were reported in more papers. " +
    (unplaced ? plural(unplaced, "variant", "variants") + " in untranslated regions are not drawn. " : "") +
    "Select a mark to see the variant's papers.";
  function drawMap() {
    var W = Math.max(320, Math.round(mapSvg.parentNode.clientWidth || 1000));
    if (W === lastW) return; lastW = W;
    // Bands are as tall as the tallest mark needs, so the map has no dead space above it.
    var most = Math.min(Math.max.apply(null, va.map(function (r) { return r.n_papers; }).concat([1])) - 1, 5);
    var left = W < 600 ? 80 : 84, right = W < 600 ? 40 : 52, tw = W - left - right;
    var base = 16 + 12 * most + 18, band = base + 40;
    mapSvg.setAttribute("viewBox", "0 0 " + W + " " + band * genes.length);
    mapSvg.replaceChildren();
    genes.forEach(function (g, gi) {
      var P = D.proteins[g], y = gi * band + base;
      function x(pos) { return left + (pos - 1) / (P.length - 1) * tw; }
      var row = svgEl("g", {});
      row.appendChild(svgEl("text", {class: "label", x: 0, y: y + 5}, [g]));
      row.appendChild(svgEl("rect", {class: "backbone", x: left, y: y - 1, width: tw, height: 2}));
      P.regions.forEach(function (rg) {
        var nc1 = /NC1/.test(rg.name), h = nc1 ? 12 : 8, x0 = x(rg.start), w = x(rg.end) - x0;
        row.appendChild(svgEl("rect", {class: nc1 ? "nc1" : "helix", x: x0, y: y - h / 2, width: w, height: h, rx: nc1 ? 2 : 1},
                          [svgEl("title", {}, [rg.name + ", residues " + rg.start + "–" + rg.end])]));
        row.appendChild(svgEl("text", {class: "region", x: x0 + w / 2, y: y + 24, "text-anchor": "middle"}, [nc1 ? "NC1" : rg.name]));
      });
      row.appendChild(svgEl("text", {class: "length", x: left + tw + 8, y: y + 4}, [P.length.toLocaleString("en")]));
      va.filter(function (r) { return r.gene === g && r.protein_position != null; })
        .sort(function (a, b) { return b.n_papers - a.n_papers; })
        .forEach(function (r) {
          var px = x(Math.min(r.protein_position, P.length)), h = 16 + 12 * Math.min(r.n_papers - 1, 5);
          var tip = short(r.variant_id) + ", residue " + r.protein_position + ": " + plural(r.n_papers, "paper", "papers") +
                    ", " + span(r.probands_min, r.probands_max) + " probands";
          var m = svgEl("g", {class: "mark" + (isGlycine(r) ? " gly" : ""), "data-key": r.variant_id}, [
            svgEl("line", {class: "stem", x1: px, x2: px, y1: y - 6, y2: y - 6 - h}),
            svgEl("circle", {class: "head", cx: px, cy: y - 6 - h, r: 3.5}),
            svgEl("title", {}, [tip])
          ]);
          m.addEventListener("click", function () { location.href = dataLink({tab: "variants", q: r.variant_id}); });
          row.appendChild(m);
        });
      mapSvg.appendChild(row);
    });
  }
  drawMap();
  window.addEventListener("resize", drawMap);

