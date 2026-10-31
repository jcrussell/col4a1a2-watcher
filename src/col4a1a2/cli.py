"""Command-line interface: fetch-papers, extract, show."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import anthropic

from col4a1a2.config import ConfigError, Settings, load_settings
from col4a1a2.discovery import DiscoveryLog, plan as plan_slices
from col4a1a2.extract.prompt import PaperInput
from col4a1a2.extract.triage import TRIAGE_PROMPT_VERSION, TriageCache, triage_paper
from col4a1a2 import jsonl, schemas
from col4a1a2.http import PoliteClient
from col4a1a2.ledger import Ledger
from col4a1a2.sources import europepmc, ncbi, pubtator
from col4a1a2.sources.europepmc import citation
from col4a1a2.sources.fulltext import TextRegion, jats_inventory, jats_to_text

log = logging.getLogger("col4a1a2")
REPO = Path(__file__).resolve().parents[2]
# Bundle members live outside any snapshot. The endpoint they come from fails or
# stalls for most requests, so a successful download is valuable and must be
# reused by every later snapshot rather than re-attempted per run.
BUNDLE_STORE = REPO / "data" / "cache" / "bundles"   # default; settings.bundle_store wins
CLINICAL_DEFAULT = (
    r"variant|mutation|pathogenic|de novo|heterozygous|c\.\d|p\.\(?[A-Z][a-z]{2}\d|Gould|HANAC|porencephal|"
    r"schizencephal|h(a)?emorrhag|small[- ]vessel|leukoencephalopath|PADMAL|cataract|anterior segment|"
    r"Axenfeld|tortuosity|hematuria|haematuria|nephropath|cramps|myopath|cerebral palsy|stroke|proband|famil"
)


def load_config(path: Path | None = None) -> Settings:
    """Back-compatible name; returns Settings now rather than a raw dict."""
    return load_settings(path)


def make_client(settings: Settings) -> PoliteClient:
    return PoliteClient(
        user_agent=settings.user_agent,
        cache_path=settings.http_cache,
        rates=settings.rates,
    )


def make_anthropic(settings: Settings) -> anthropic.Anthropic:
    """The one place an Anthropic client is constructed.

    The SDK defaults to two retries with 0.5s/8s backoff, which we were taking
    silently in five separate places. Worst case per call is
    timeout x (retries + 1), so both come from config and stay bounded.
    """
    return anthropic.Anthropic(
        max_retries=settings.anthropic_max_retries,
        timeout=settings.anthropic_timeout_s,
    )


def snapshot_dir(arg: str | None, settings: Settings | None = None) -> Path:
    root = settings.snapshot_root if settings else (REPO / "data" / "raw")
    d = Path(arg) if arg else root / dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_jsonl(path: Path, rows) -> int:
    return jsonl.write(path, rows)


def read_jsonl(path: Path) -> list[dict]:
    return jsonl.read(path)


# ---------------------------------------------------------------- fetch-papers

@dataclass
class _Enrichment:
    """What the supplement and figure stages added to one paper."""
    supplements: list[tuple[str, str]] = dc_field(default_factory=list)
    figures: list[tuple[str, str]] = dc_field(default_factory=list)
    bundle_summary: dict = dc_field(default_factory=dict)
    supplement_meta: list[dict] = dc_field(default_factory=list)
    figure_meta: list[dict] = dc_field(default_factory=list)
    gate: dict = dc_field(default_factory=dict)
    vision_usage: dict = dc_field(default_factory=lambda: {"input": 0, "output": 0, "calls": 0})


def _enrich(
    client: PoliteClient,
    pt,
    xml: bytes,
    pmcid: str,
    dest: Path,
    *,
    want_supplements: bool,
    want_figures: bool,
    force_figures: bool,
    acl=None,
    fig_cache=None,
) -> _Enrichment:
    """Fetch the article's file bundle, parse its tables, and transcribe figures.

    Ordering matters: supplements are added before the figure gate is evaluated,
    so a paper whose supplementary table supplies the variants never pays for
    vision and never gains model-transcribed evidence it does not need.
    """
    from col4a1a2.sources import supplements as sup
    from col4a1a2.sources import tabular

    e = _Enrichment()
    if not (want_supplements or want_figures):
        return e

    bundle = sup.load_cached(dest)
    if bundle is None:
        bundle = sup.fetch_bundle(client, pmcid)
    else:
        log.info("%s: reusing %d bundle members already on disk", pmcid, len(bundle.members))
    if not bundle.ok:
        e.bundle_summary = bundle.summary()
        log.info("%s: no usable bundle (%s)", pmcid, bundle.skip_reason or f"HTTP {bundle.status}")
        return e

    figs, sups = jats_inventory(xml)
    sup.classify(bundle, figs, sups)
    # Summarise after classifying, or every member still counts as unmatched.
    e.bundle_summary = bundle.summary()
    sup.write_members(bundle, dest)

    if want_supplements:
        # One budget for the whole paper, not per file: a paper with eight parsed
        # supplements would otherwise contribute eight times the cap.
        char_budget = tabular.MAX_CHARS_PER_PAPER
        # Keyed by basename and by stem, because some publishers emit hrefs with
        # no extension; without the stem fallback the region would be labelled
        # "mmc1.xlsx" instead of "eTable 2", in both the prompt and the provenance.
        label_for: dict[str, str] = {}
        for s_ref in sups:
            for href in s_ref.hrefs:
                base = Path(href).name
                label = s_ref.label or s_ref.caption[:60]
                label_for.setdefault(base, label)
                label_for.setdefault(Path(base).stem, label)
        for m in bundle.by_kind("supplement"):
            meta = {"member": m.name, "ext": m.ext, "bytes": m.size,
                    "label": label_for.get(m.name) or label_for.get(m.stem, ""),
                    "sheets": [], "skip_reason": ""}
            if m.ext not in tabular.SUPPORTED:
                meta["skip_reason"] = f"unsupported extension {m.ext!r}"
                e.supplement_meta.append(meta)
                continue
            try:
                sheets = tabular.filter_sheets(tabular.parse(m.data, m.ext))
            except tabular.UnsupportedSupplement as exc:
                meta["skip_reason"] = str(exc)
                e.supplement_meta.append(meta)
                continue
            sheets = [sh for sh in sheets if sh.rows]
            if sheets:
                text, rendered = tabular.sheets_to_text(
                    meta["label"], m.name, sheets, max_chars=char_budget)
                # Record only the sheets that actually reached the prompt; claiming
                # the rest would be exactly the silent truncation this guards against.
                meta["sheets"] = [sh.summary() for sh in sheets[:rendered]]
                meta["sheets_dropped_at_cap"] = len(sheets) - rendered
                if rendered:
                    e.supplements.append((meta["label"] or m.name, text))
                    char_budget -= len(text)
            e.supplement_meta.append(meta)

    if want_figures and acl is not None and fig_cache is not None:
        from col4a1a2.extract import figures as figmod

        probe = type(pt)(pmid=pt.pmid, pmcid=pt.pmcid, title=pt.title, abstract=pt.abstract,
                         body=pt.body, n_tables=pt.n_tables, supplements=list(e.supplements))
        chosen, gate = figmod.select_figures(figs, probe.as_prompt_text(), force=force_figures)
        e.gate = gate
        for fig in chosen:
            member = next((bundle.find(h) for h in fig.graphic_hrefs if bundle.find(h)), None)
            meta = {"fig_id": fig.fig_id, "label": fig.label, "member": member.name if member else "",
                    "selected": True, "select_reason": gate.get("selected_reason", ""),
                    "transcribed": False, "skip_reason": ""}
            if member is None or member.data is None:
                meta["skip_reason"] = "no readable image member"
                e.figure_meta.append(meta)
                continue
            mt = figmod.media_type_for(member.ext)
            if mt is None:
                meta["skip_reason"] = f"unsupported image type {member.ext!r}"
                e.figure_meta.append(meta)
                continue
            digest = fig_cache.digest(member.data)
            meta["image_sha256"] = digest
            cached = fig_cache.get(digest)
            if cached is None:
                try:
                    tr, usage = figmod.transcribe_figure(acl, member.data, mt, fig)
                    cached = fig_cache.put(pmcid=pmcid, fig=fig, image_sha256=digest,
                                           transcription=tr, usage=usage)
                except Exception as exc:
                    # A figure is optional enrichment. Letting one bad image abort
                    # the fetch would discard every paper gathered so far, along
                    # with the rate-limited downloads and the paid vision calls.
                    meta["skip_reason"] = f"transcription failed: {type(exc).__name__}: {exc}"
                    log.warning("%s %s: %s", pmcid, fig.fig_id, exc)
                    e.figure_meta.append(meta)
                    continue
                e.vision_usage["input"] += usage["input"]
                e.vision_usage["output"] += usage["output"]
                e.vision_usage["calls"] += 1
            tr = figmod.FigureTranscription.model_validate(cached["transcription"])
            meta.update({"transcribed": True, "legible": tr.legible,
                         "contains_variant_labels": tr.contains_variant_labels,
                         "figure_prompt_version": cached.get("figure_prompt_version", "")})
            if tr.contains_variant_labels and tr.items:
                e.figures.append((figmod.transcript_label(fig, tr), figmod.transcription_to_text(fig, tr)))
            else:
                meta["skip_reason"] = "no variant labels in figure"
            e.figure_meta.append(meta)
    return e


def cmd_fetch_papers(a: argparse.Namespace) -> int:
    st: Settings = a.settings
    client = make_client(st)
    snap = snapshot_dir(a.snapshot, a.settings)
    email = st.contact_email

    # Time-sliced discovery. A sealed slice is never queried again, so a monthly
    # run stops re-downloading the whole historical sweep (303 cached search
    # rows, 17.4 MB) to find twenty new papers.
    dlog = DiscoveryLog(st.curated_dir / "discovery.jsonl")
    todo, sealed = plan_slices(a.date_from or "2000-01-01",
                               a.date_to or dt.date.today().isoformat(),
                               st.europepmc_query, dlog, rediscover=a.rediscover)
    # Every slice is still queried, so the candidate set never shrinks. What
    # sealing changes is the cache lifetime: a sealed slice's query is immutable
    # -- a closed date range against a fixed query string -- so its answer is
    # cached forever rather than expiring on the 7-day TTL that makes a monthly
    # run re-download the whole historical sweep. Only the open window pays.
    sealed_keys = {sl.key for sl in sealed}
    log.info("Europe PMC: %d slices, %d sealed (served from cache permanently)",
             len(todo) + len(sealed), len(sealed))
    epmc: list[europepmc.PaperMeta] = []
    for sl in sorted(todo + sealed, key=lambda s: s.date_from):
        is_sealed_slice = sl.key in sealed_keys
        got = europepmc.search(
            client, st.europepmc_query, date_from=sl.date_from, date_to=sl.date_to,
            page_size=st.europepmc_page_size, max_results=a.max_search,
            ttl=None if is_sealed_slice else 7 * 86400,
        )
        epmc.extend(got)
        if is_sealed_slice:
            continue
        available = None
        try:
            available = europepmc.hit_count(
                client,
                f"({st.europepmc_query}) AND FIRST_PDATE:[{sl.date_from} TO {sl.date_to}]",
            )
        except Exception as e:  # coverage reporting must never break a fetch
            log.debug("hit_count failed for %s: %s", sl.key, e)
        # A slice truncated by --max-search has not been covered, so recording it
        # complete would silently freeze a partial sweep.
        complete = available is None or len(got) >= available
        dlog.record(query=st.europepmc_query, sl=sl, hits_available=available,
                    hits_fetched=len(got), complete=complete)
        if available is not None and not complete:
            log.warning("slice %s: fetched %d of %d available", sl.key, len(got), available)
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    by_pmid: dict[str, europepmc.PaperMeta] = {}
    n_no_pmid = 0
    for m in epmc:
        if m.pmid:
            by_pmid.setdefault(m.pmid, m)
        else:
            # Write point 1. These vanished entirely before: no snapshot row, no
            # triage decision, only an aggregate in a log line. Identity falls
            # back to DOI, then to a title hash.
            n_no_pmid += 1
            ledger.record_candidate(
                pmid=None, doi=m.doi, title=m.title, journal=m.journal,
                first_pub_date=m.first_pub_date, is_open_access=m.is_open_access,
                has_pmcid=bool(m.pmcid), found_by={"europepmc"},
                dropped_at="no_pmid", snapshot=snap.name,
            )
    log.info("Europe PMC: %d results, %d with PMID", len(epmc), len(by_pmid))

    pubtator_hits: dict[str, dict] = {}
    n_lookup_requested = n_lookup_resolved = 0
    if not a.skip_pubtator:
        for q in st.pubtator_queries:
            res = pubtator.search(client, q, page_size=st.pubtator_page_size, max_results=a.max_search)
            for r in res:
                pmid = str(r.get("pmid") or "")
                date = (r.get("date") or "")[:10]
                if not pmid:
                    continue
                if a.date_from and date and date < a.date_from:
                    continue
                if a.date_to and date and date > a.date_to:
                    continue
                pubtator_hits.setdefault(pmid, r)
        log.info("PubTator: %d PMIDs in window", len(pubtator_hits))
    # PMIDs only PubTator found: look them up in Europe PMC to get PMCID/OA/abstract.
    missing = [p for p in pubtator_hits if p not in by_pmid]
    resolved = europepmc.lookup_pmids(client, missing)
    for pmid, m in resolved.items():
        if pmid not in by_pmid:
            m.source = "pubtator"
            by_pmid[pmid] = m
    for pmid in missing:
        if pmid not in by_pmid:
            # Nothing is known about this paper beyond PubTator's own row, so
            # `is_open_access=False` here means "not retrieved". `metadata_source`
            # carries that distinction to the open-access filter and the ledger,
            # which used to record it as closed access on no evidence at all.
            r = pubtator_hits[pmid]
            by_pmid[pmid] = europepmc.PaperMeta(pmid=pmid, pmcid=None, doi=r.get("doi"), title=r.get("title", ""),
                                                journal=r.get("journal"), pub_year=None,
                                                first_pub_date=(r.get("date") or "")[:10] or None,
                                                is_open_access=False, abstract=None, source="pubtator",
                                                metadata_source="pubtator_only")
    n_lookup_requested, n_lookup_resolved = len(missing), len(resolved)

    metas = list(by_pmid.values())
    # Captured before the filter reassigns `metas`: the manifest has been
    # reporting a post-filter number under this name.
    n_candidates_before_filters = len(metas)
    if a.oa_only:
        kept_oa = [m for m in metas if m.is_open_access and m.pmcid]
        # Write point 2. The filter runs before the triage loop, so a paper cut
        # here never gets a decision row and used to leave no trace at all.
        # Partitioned by object identity: `m not in kept_oa` compares dataclass
        # fields down a list, which is quadratic and the candidate pool is now
        # thousands of papers.
        keep_ids = {id(m) for m in kept_oa}
        dropped = [m for m in metas if id(m) not in keep_ids]
        for m in dropped:
            ledger.record_candidate(
                pmid=m.pmid, doi=m.doi, title=m.title, journal=m.journal,
                first_pub_date=m.first_pub_date, is_open_access=m.is_open_access,
                has_pmcid=bool(m.pmcid), found_by={m.source}, citation=citation(m),
                dropped_at=("no_metadata" if m.metadata_source != "europepmc" else "oa_filter"),
                snapshot=snap.name,
            )
        log.info("open-access filter: dropped %d of %d candidates", len(dropped), len(metas))
        metas = kept_oa

    # Triage before downloading any full text: PubTator annotations come from the
    # title/abstract export and are cheap; full texts are fetched only for kept papers.
    annots = pubtator.export_biocjson(client, [m.pmid for m in metas if m.pmid]) if not a.skip_pubtator else {}
    gene_ids = set(st.pubtator_gene_ids)
    gene_re = re.compile(r"\bCOL4A[12]\b", re.I)
    # The regex is a classifier, versioned with the code that uses it, and it is
    # immune to TOML/YAML string-literal hazards here. It is deliberately not config.
    clinical_re = re.compile(CLINICAL_DEFAULT, re.I)

    # The LLM gate runs on title + abstract only, before any full text is fetched.
    # Without a key (or with --no-llm-triage) we fall back to the keyword rule,
    # which has the same recall on the 2026-10-29 snapshot but keeps 3x the noise.
    use_llm = not a.no_llm_triage and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not use_llm and not a.no_llm_triage:
        log.warning("ANTHROPIC_API_KEY not set; falling back to keyword triage")
    tcache = TriageCache(st.triage_cache)
    acl = make_anthropic(st) if use_llm else None

    triaged: list[tuple[europepmc.PaperMeta, list[dict], dict]] = []
    for m in metas:
        doc = annots.get(m.pmid, {})
        hints = pubtator.annotations(doc) if m.pmid in annots else []
        linked = pubtator.gene_linked_variants(doc, gene_ids) if doc else []
        ta = f"{m.title} {m.abstract or ''}"
        tr = {
            "title_abstract_mention": bool(gene_re.search(ta)),
            "clinical_keyword": bool(clinical_re.search(ta)),
            "pubtator_gene_variant": any(h["type"] == "Gene" and str(h.get("identifier")) in gene_ids for h in hints)
                                     and any(h["type"] in ("Variant", "Mutation") for h in hints),
            "pubtator_gene_linked_variant": bool(linked),
            "pubtator_linked_hgvs": [v["hgvs"] for v in linked if v.get("hgvs")],
            "pubtator_search_hit": m.pmid in pubtator_hits,
        }
        keyword_keep = (tr["pubtator_search_hit"] or tr["pubtator_gene_variant"]
                        or (tr["title_abstract_mention"] and tr["clinical_keyword"]))
        tr["keyword_keep"] = keyword_keep
        if use_llm:
            cached = tcache.get(m.pmid)
            if cached is None:
                try:
                    decision, usage = triage_paper(acl, m.title, m.abstract)
                    cached = tcache.put(m.pmid, decision, usage)
                except Exception as e:  # never let triage abort a fetch
                    log.warning("triage failed for %s (%s); keeping paper", m.pmid, type(e).__name__)
                    cached = {"read_full_text": True, "reason": f"triage error: {type(e).__name__}",
                              "triage_prompt_version": TRIAGE_PROMPT_VERSION}
            tr["llm_keep"] = bool(cached["read_full_text"])
            tr["llm_reason"] = cached.get("reason", "")
            tr["triage_prompt_version"] = cached.get("triage_prompt_version", TRIAGE_PROMPT_VERSION)
            # A PubTator variant mention normalised to COL4A1/COL4A2 scored precision
            # 1.00 on the snapshot, so it overrides a negative model decision.
            tr["keep"] = tr["llm_keep"] or tr["pubtator_gene_linked_variant"]
        else:
            tr["keep"] = keyword_keep
        triaged.append((m, hints, tr))

    triaged.sort(key=lambda x: (x[2]["keep"], x[2]["pubtator_gene_linked_variant"],
                                x[2]["pubtator_search_hit"], x[0].first_pub_date or ""), reverse=True)
    n_keep_all = sum(1 for _, _, tr in triaged if tr["keep"])
    log.info("triage: %d of %d papers kept (%s); cache hits %d, new calls %d",
             n_keep_all, len(metas), "llm" if use_llm else "keyword", tcache.hits, tcache.misses)
    # Write point 3. Recorded for every triaged candidate, including the ones
    # `--limit` is about to discard, which previously survived only as a bare
    # triage decision with no title, journal, date or identifier.
    for m, _hints, tr in triaged:
        # No disposition: a paper `--limit` did not reach this run is still in
        # play, and recording otherwise let a later limited run bury work
        # already done. The per-run count goes in the manifest instead, and
        # `col4a1a2 ledger` derives the backlog from triage and stage.
        ledger.record_candidate(
            pmid=m.pmid, doi=m.doi, title=m.title, journal=m.journal,
            first_pub_date=m.first_pub_date, is_open_access=m.is_open_access,
            has_pmcid=bool(m.pmcid), found_by={m.source}, citation=citation(m),
            dropped_at="", snapshot=snap.name,
        )
        if m.pmid:
            ledger.record_triage(m.pmid, {k: tr.get(k) for k in
                                          ("keep", "llm_keep", "llm_reason",
                                           "pubtator_gene_linked_variant")})
    n_triaged = len(triaged)
    if a.limit:
        triaged = triaged[: a.limit]

    want_supplements = not a.no_supplements
    want_figures = not a.no_figures and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not a.no_figures and not want_figures:
        log.warning("ANTHROPIC_API_KEY not set; skipping figure transcription")
    acl = make_anthropic(st) if want_figures else None
    fig_cache = None
    if want_figures:
        from col4a1a2.extract.figures import FigureCache
        fig_cache = FigureCache(st.figure_cache)

    rows = []
    n_full = 0
    n_sup_files = 0
    n_fig_read = 0
    vision_usage = {"input": 0, "output": 0, "calls": 0}
    for m, hints, triage in triaged:
        xml = None
        fulltext_source = None
        if m.pmcid and (triage["keep"] or a.fetch_all_texts):
            xml = europepmc.full_text_xml(client, m.pmcid)
            fulltext_source = "europepmc" if xml else None
            if not xml:
                xml = ncbi.pmc_full_text_xml(client, m.pmcid, email)
                fulltext_source = "ncbi" if xml else None
        if xml:
            pt = jats_to_text(xml)
            abstract_only = False
            n_full += 1
            (snap / "fulltext").mkdir(exist_ok=True)
            (snap / "fulltext" / f"{m.pmcid}.xml").write_bytes(xml)
            n_tables = pt.n_tables
            enr = _Enrichment()
            if triage["keep"] and (want_supplements or want_figures):
                try:
                    enr = _enrich(
                        client, pt, xml, m.pmcid, st.bundle_store / m.pmcid,
                        want_supplements=want_supplements, want_figures=want_figures,
                        force_figures=a.figures, acl=acl, fig_cache=fig_cache,
                    )
                except Exception as exc:
                    # Enrichment is optional. Letting it raise would discard every
                    # paper fetched so far, along with the rate-limited downloads
                    # and the paid vision calls already spent on them.
                    log.warning("%s: enrichment failed (%s: %s)", m.pmcid, type(exc).__name__, exc)
                    enr = _Enrichment(bundle_summary={"skip_reason": f"{type(exc).__name__}: {exc}"})
                pt.supplements = enr.supplements
                pt.figures = enr.figures
                n_sup_files += len(enr.supplements)
                n_fig_read += len(enr.figures)
                for k in ("input", "output", "calls"):
                    vision_usage[k] += enr.vision_usage[k]
            text, regions = pt.as_prompt_text(), pt.regions()
        else:
            text = f"TITLE: {m.title}\n\nABSTRACT:\n{m.abstract or '(no abstract available)'}"
            abstract_only, n_tables, regions = True, 0, []
            enr = _Enrichment()
        rows.append({
            "pmid": m.pmid, "pmcid": m.pmcid, "doi": m.doi, "title": m.title, "journal": m.journal,
            "first_pub_date": m.first_pub_date, "pub_year": m.pub_year,
            "author_string": m.author_string, "authors": m.authors, "affiliation": m.affiliation,
            "abstract": m.abstract,
            "citation": citation(m),
            "is_open_access": m.is_open_access, "source": m.source,
            "abstract_only": abstract_only, "n_tables": n_tables, "n_chars": len(text), "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "regions": [{"kind": r.kind, "label": r.label, "start": r.start, "end": r.end} for r in regions],
            "fulltext_source": fulltext_source,
            "supplement_bundle": enr.bundle_summary,
            "supplements": enr.supplement_meta,
            "figures": enr.figure_meta,
            "figure_gate": enr.gate,
            "n_chars_supplements": sum(len(t) for _, t in enr.supplements),
            "n_chars_figures": sum(len(t) for _, t in enr.figures),
            "pubtator_hints": hints, "triage": triage,
        })
    for r in rows:
        prev = ledger._state.get(r["pmid"] or "") or {}
        if r["pmid"] and prev.get("text_sha256") != r["text_sha256"]:
            ledger.record(identity=r["pmid"], stage="fetched", snapshot=snap.name,
                          text_sha256=r["text_sha256"], n_chars=r["n_chars"],
                          abstract_only=r["abstract_only"],
                          fulltext_source=r.get("fulltext_source"),
                          bundle_status=(r.get("supplement_bundle") or {}).get("status"),
                          n_supplements=len([x for x in (r.get("supplements") or []) if x.get("sheets")]),
                          n_figures=len([x for x in (r.get("figures") or []) if x.get("transcribed")]))
    log.info("ledger: %d events appended to %s", ledger.appended, ledger.path)
    n = write_jsonl(snap / "papers.jsonl", rows)
    manifest = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "date_from": a.date_from, "date_to": a.date_to, "limit": a.limit, "oa_only": a.oa_only,
        "europepmc_query": st.europepmc_query, "pubtator_queries": st.pubtator_queries,
        "papers": n, "full_text": n_full, "abstract_only": n - n_full,
        "triage_keep": sum(1 for r in rows if r["triage"]["keep"]), "triage_keep_before_limit": n_keep_all,
        "candidates_triaged": n_triaged,
        "candidates_cut_by_limit": max(0, n_triaged - a.limit) if a.limit else 0,
        "candidates_before_filters": n_candidates_before_filters,
        "europepmc_hits_without_pmid": n_no_pmid,
        # Requested against resolved, because the old lookup lost 98% of its
        # PMIDs silently: the fallback invented metadata and nothing compared
        # the two numbers. A drop here is the first sign of the next break.
        "pmid_lookup_requested": n_lookup_requested,
        "pmid_lookup_resolved": n_lookup_resolved,
        "ledger_events": ledger.appended,
        "discovery_slices": len(todo) + len(sealed),
        "discovery_slices_sealed": len(sealed),
        "supplement_files_parsed": n_sup_files,
        "figures_transcribed": n_fig_read,
        "figure_vision_calls": vision_usage["calls"],
        "figure_vision_tokens": {"input": vision_usage["input"], "output": vision_usage["output"]},
        "figure_cache_hits": fig_cache.hits if fig_cache else 0,
        "network_requests": client.network_requests, "cache_hits": client.cache_hits,
        "rate_limiter_slept_s": round(client.limiter.slept_total, 1),
    }
    # One document per snapshot directory, so JSON rather than JSONL: JSONL is
    # for records that accumulate, and `jq -s . data/raw/*/manifest.json` gives
    # the cross-run table anyway. Schema-checked like everything else, and
    # indented because this is the one artefact a person reads in a diff.
    manifest["snapshot"] = snap.name
    schemas.write_document(snap / "manifest.json", manifest)
    client.close()
    print(json.dumps(manifest, indent=2))
    return 0


# ---------------------------------------------------------------- extract

def _paper_inputs(snap: Path) -> list[PaperInput]:
    return [
        PaperInput(pmid=r["pmid"], pmcid=r.get("pmcid"), title=r.get("title", ""), text=r["text"],
                   abstract_only=r["abstract_only"], pubtator_hints=r.get("pubtator_hints", []),
                   journal=r.get("journal"), pub_date=r.get("first_pub_date"),
                   regions=[TextRegion(**d) for d in r.get("regions", [])])
        for r in read_jsonl(snap / "papers.jsonl")
        if r.get("triage", {}).get("keep", True) or getattr(_paper_inputs, "include_all", False)
    ]


def cmd_extract(a: argparse.Namespace) -> int:
    import anthropic
    from col4a1a2.extract.client import (
        DEFAULT_MODEL,
        ExtractionRefused,
        ExtractionTruncated,
        count_tokens,
        extract_paper,
    )
    from pydantic import ValidationError as PydanticValidationError

    from col4a1a2.extract.prompt import PROMPT_VERSION
    from col4a1a2.extract.verify import ground

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; export it and re-run.", file=sys.stderr)
        return 2
    st: Settings = a.settings
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    prior = ledger.fold()
    # argparse defaults are None so config can win; the CLI flag still beats it.
    model = a.model or st.extract_model
    effort = a.effort or st.extract_effort
    snap = snapshot_dir(a.snapshot, st)
    _paper_inputs.include_all = a.all
    papers = _paper_inputs(snap)
    if a.pmid:
        # Named papers only, for piloting a prompt or schema change against
        # papers whose previous answers and challenge verdicts are known.
        wanted = set(a.pmid)
        papers = [p for p in papers if p.pmid in wanted]
        missing = wanted - {p.pmid for p in papers}
        if missing:
            print(f"not in this snapshot or not kept by the gate: {', '.join(sorted(missing))}",
                  file=sys.stderr)
    if a.limit:
        papers = papers[: a.limit]
    client = make_anthropic(st)
    out_path = snap / "extractions.jsonl"
    # Resume on (prompt version, text hash). Keying on the prompt version alone
    # silently skipped papers whose text had grown — e.g. once supplements and
    # figure transcriptions were appended — making those additions a no-op.
    # Rows recording a failure are a log, not a result: including them would mark
    # a transiently refused paper done forever.
    done = {
        (r["pmid"], r.get("text_sha256"))
        for r in read_jsonl(out_path)
        if r.get("prompt_version") == PROMPT_VERSION and not r.get("error")
    }
    # Resume across snapshots, not just within one. Without this a paper
    # extracted in last month's snapshot is re-extracted in this month's at full
    # price, which is the whole reason the bootstrap could not be split into
    # affordable pieces. The ledger is the cross-snapshot index.
    for ident, pr in prior.items():
        if (pr.get("prompt_version") == PROMPT_VERSION
                and pr.get("stage") == "extracted"
                and not pr.get("last_error")
                and pr.get("text_sha256")):
            done.add((ident, pr["text_sha256"]))
    texts = {p.pmid: p.text for p in papers}
    regions_by_pmid = {p.pmid: p.regions for p in papers}

    if a.count_only:
        total = 0
        for p in papers:
            n = count_tokens(client, p, model=model)
            total += n
            print(f"{p.pmid}\t{'abstract' if p.abstract_only else 'fulltext'}\t{n} tokens")
        print(f"TOTAL\t{len(papers)} papers\t{total} input tokens\tmean {total // max(1, len(papers))}")
        return 0

    n_failed = 0
    n_ok = 0
    n_skipped = 0
    spent = 0.0
    price = st.price_for(model)
    with out_path.open("a", encoding="utf-8") as f:
        for p in papers:
            text_hash = hashlib.sha256(p.text.encode("utf-8")).hexdigest()
            if (p.pmid, text_hash) in done:
                log.info("skip %s (already extracted at %s on identical text)", p.pmid, PROMPT_VERSION)
                n_skipped += 1
                continue
            if a.budget_usd and spent >= a.budget_usd:
                log.warning("budget of $%.2f reached after %d papers; stopping", a.budget_usd, n_ok)
                break
            log.info("extracting %s (%s)", p.pmid, "abstract" if p.abstract_only else "full text")
            try:
                res = extract_paper(client, p, model=model, effort=effort)
            except (ExtractionRefused, ExtractionTruncated, PydanticValidationError) as e:
                # One unusable paper must not abort the batch; record why and move on.
                log.warning("%s: %s", p.pmid, e)
                f.write(jsonl.dumps({"pmid": p.pmid, "pmcid": p.pmcid, "prompt_version": PROMPT_VERSION,
                                     "text_sha256": text_hash, "error": type(e).__name__,
                                     "detail": str(e)}) + "\n")
                f.flush()
                n_failed += 1
                ledger.record(identity=p.pmid, stage="fetched", snapshot=snap.name,
                              text_sha256=text_hash, prompt_version=PROMPT_VERSION,
                              last_error=type(e).__name__,
                              attempts=(prior.get(p.pmid, {}).get("attempts") or 0) + 1)
                continue
            rep = ground(res.extraction, texts[p.pmid], regions_by_pmid.get(p.pmid, []))
            row = {
                "pmid": p.pmid, "pmcid": p.pmcid, "model": res.model, "prompt_version": res.prompt_version,
                "text_sha256": text_hash,
                "schema_version": res.schema_version, "stop_reason": res.stop_reason,
                "usage": {"input": res.input_tokens, "output": res.output_tokens,
                          "cache_read": res.cache_read_tokens, "cache_write": res.cache_write_tokens},
                "grounding": {"total": rep.total_quotes, "grounded": rep.grounded_quotes,
                              "ungrounded": rep.ungrounded, "low_confidence": rep.low_confidence_variants,
                              "region_counts": rep.region_counts,
                              "quote_regions": rep.quote_regions,
                              "figure_sourced": rep.figure_sourced,
                              "model_transcribed_only": rep.model_transcribed_only,
                              "overconfident_transcribed": rep.overconfident_transcribed,
                              "needs_review": rep.needs_review},
                "extraction": json.loads(res.raw_json),
            }
            f.write(jsonl.dumps(row) + "\n")
            f.flush()
            ex = res.extraction
            n_ok += 1
            if price:
                spent += price.cost(input=res.input_tokens, output=res.output_tokens,
                                    cache_read=res.cache_read_tokens,
                                    cache_write=res.cache_write_tokens)
            ledger.record(identity=p.pmid, stage="extracted", snapshot=snap.name,
                          text_sha256=text_hash, prompt_version=res.prompt_version,
                          n_variants=len(ex.variants), n_grounded=rep.grounded_quotes,
                          n_ungrounded=len(rep.ungrounded), needs_review=rep.needs_review,
                          n_figure_sourced=len(rep.figure_sourced),
                          last_error=None, attempts=0)
            print(f"{p.pmid}: {ex.paper_type.value}, variants={len(ex.variants)}, "
                  f"quotes grounded {rep.grounded_quotes}/{rep.total_quotes}, "
                  f"tokens in={res.input_tokens} out={res.output_tokens} cache_read={res.cache_read_tokens}"
                  + ("  [REVIEW]" if rep.needs_review else ""))
    print(f"\n{n_ok} extracted, {n_skipped} already done, {n_failed} failed, ${spent:.2f} spent")
    if ledger.appended:
        log.info("ledger: %d events appended", ledger.appended)
    return 0


# ---------------------------------------------------------------- challenge

def cmd_challenge(a: argparse.Namespace) -> int:
    """Adversarially review extracted records, and verify the reviewer's output."""
    import anthropic  # noqa: F401  (imported for its exceptions via make_anthropic)

    from col4a1a2.extract.challenge import (
        ChallengeCache,
        ChallengeFailed,
        challenge_paper,
        check,
        records_digest,
        slim_records,
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; export it and re-run.", file=sys.stderr)
        return 2
    st: Settings = a.settings
    snap = snapshot_dir(a.snapshot, st)
    papers = {r["pmid"]: r for r in read_jsonl(snap / "papers.jsonl")}
    rows = latest_extractions(snap / "extractions.jsonl")
    if a.pmid:
        rows = [r for r in rows if r["pmid"] == a.pmid]
    # Every extracted paper, including those with no records: a zero-record
    # extraction is either an empty paper or a total miss, and only
    # missed_variants distinguishes them.
    rows = [r for r in rows if r["pmid"] in papers]
    if a.limit:
        rows = rows[: a.limit]

    cache = ChallengeCache(st.curated_dir / "challenges.jsonl")
    client = make_anthropic(st)
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    spent = 0.0
    price = st.price_for(st.extract_model)
    n_done = n_failed = 0

    for r in rows:
        pmid = r["pmid"]
        paper = papers[pmid]
        text = paper["text"]
        recs = slim_records(pmid, r["extraction"].get("variants") or [],
                            (r.get("grounding") or {}).get("figure_sourced") or [])
        digest = records_digest(recs)
        cached = cache.get(paper.get("text_sha256", ""), digest)
        if cached is not None:
            log.info("skip %s (challenged at %s on identical text and records)", pmid,
                     cached.get("challenge_prompt_version"))
            continue
        if a.budget_usd and spent >= a.budget_usd:
            log.warning("budget of $%.2f reached after %d papers; stopping", a.budget_usd, n_done)
            break
        log.info("challenging %s (%d records)", pmid, len(recs))
        try:
            ch, usage = challenge_paper(client, pmid=pmid, source_text=text, records=recs,
                                        model=st.extract_model, effort=a.effort)
        except (ChallengeFailed, Exception) as e:  # fails closed: never "nothing to refute"
            if isinstance(e, KeyboardInterrupt):
                raise
            log.warning("%s: challenge failed (%s: %s)", pmid, type(e).__name__, e)
            n_failed += 1
            continue
        rep = check(ch, recs, text)
        cache.put(pmid=pmid, text_sha256=paper.get("text_sha256", ""), records_digest=digest,
                  model=st.extract_model, challenge=ch, report=rep, usage=usage)
        if price:
            spent += price.cost(input=usage["input"], output=usage["output"],
                                cache_read=usage["cache_read"])
        ledger.record(identity=pmid, stage="challenged", snapshot=snap.name,
                      challenge={k: rep.as_dict()[k] for k in
                                 ("n_records", "n_upheld", "n_disputed", "n_unverifiable",
                                  "n_fabricated", "incomplete")})
        n_done += 1
        flags = []
        if rep.incomplete:
            flags.append("INCOMPLETE")
        if rep.fabricated:
            flags.append(f"{len(rep.fabricated)} FABRICATED")
        print(f"{pmid}: {len(recs)} records -> upheld={len(rep.upheld)} disputed={len(rep.disputed)} "
              f"unverifiable={len(rep.unverifiable)} missed={len(rep.missed_grounded)}"
              f"{'  ' + ' '.join(flags) if flags else ''}  [{usage['effort']}]")

    print(f"\n{n_done} challenged, {n_failed} failed, cache hits {cache.hits}, ${spent:.2f} spent")
    return 0


# ---------------------------------------------------------------- review

# The spot-check sheet's columns, declared rather than derived from whatever the
# first queued record happens to carry. The two CURATOR_ fields are left empty
# for a human to fill, and are what `data/curated/gold/` is built from.
REVIEW_COLUMNS = [
    "pmid", "record_key", "citation", "gene", "as_written", "hgvs_c", "hgvs_p",
    "n_probands", "n_families", "confidence", "flagged_because", "verdict",
    "problem", "refuting_quote", "proposed_n_probands", "quote_1",
    "CURATOR_VERDICT", "CURATOR_NOTE",
]


def cmd_review(a: argparse.Namespace) -> int:
    """Emit the human spot-check queue as TSV, from both review signals."""
    import csv as _csv

    st: Settings = a.settings
    snap = snapshot_dir(a.snapshot, st)
    papers = {r["pmid"]: r for r in read_jsonl(snap / "papers.jsonl")}
    rows = latest_extractions(snap / "extractions.jsonl")

    from col4a1a2.extract.challenge import record_key, slim_records
    challenges: dict[str, dict] = {}
    cpath = st.curated_dir / "challenges.jsonl"
    if cpath.exists():
        for line in cpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                challenges[c["pmid"]] = c

    def clean(v) -> str:
        # The measurement that justified TSV covered extraction fields only;
        # challenge output adds free text, so reject separators rather than assume.
        return " ".join(str(v or "").split()).replace("\t", " ")

    out = []
    for r in rows:
        pmid = r["pmid"]
        g = r.get("grounding") or {}
        ch = challenges.get(pmid, {})
        verdicts = {v["record_key"]: v for v in (ch.get("challenge") or {}).get("verdicts", [])}
        rep = ch.get("report") or {}
        for i, v in enumerate(r["extraction"].get("variants") or []):
            key = record_key(pmid, i, v)
            reasons = []
            if v["as_written"] in (g.get("figure_sourced") or []):
                reasons.append("figure-sourced")
            if v["confidence"] == "low":
                reasons.append("low-confidence")
            if any(u[0] == v["as_written"] for u in g.get("ungrounded") or []):
                reasons.append("ungrounded-quote")
            vd = verdicts.get(key, {})
            if vd.get("verdict") == "disputed" and key in (rep.get("disputed") or []):
                reasons.append("disputed")
            if not reasons:
                continue
            out.append({
                "pmid": pmid, "record_key": key,
                "citation": clean(papers.get(pmid, {}).get("citation")),
                "gene": v["gene"], "as_written": clean(v["as_written"]),
                "hgvs_c": v.get("hgvs_c") or "", "hgvs_p": v.get("hgvs_p") or "",
                "n_probands": v["n_probands"], "n_families": v["n_families"],
                "confidence": v["confidence"], "flagged_because": "+".join(reasons),
                "verdict": vd.get("verdict", ""),
                "problem": clean(vd.get("problem"))[:200],
                "refuting_quote": clean(vd.get("refuting_quote"))[:300],
                "proposed_n_probands": vd.get("proposed_n_probands") or "",
                "quote_1": clean((v["evidence_quotes"] or [""])[0])[:300],
                "CURATOR_VERDICT": "", "CURATOR_NOTE": "",
            })
    # Scoped to this snapshot's papers. Reading every challenge ever written
    # would attribute corpus-wide counts to one snapshot, which is how the
    # same 275 reports were printed under each of four snapshots.
    in_snapshot = {r["pmid"] for r in rows}
    missed = [(p, m) for p, c in challenges.items() if p in in_snapshot
              for m in (c.get("report") or {}).get("missed_grounded", [])]
    # JSONL is the artefact; it has a schema and nothing downstream parses TSV.
    # Written even when empty -- a curator finding an empty queue and a curator
    # finding no file are different situations, and deriving the columns from
    # the first row crashed on the one snapshot that had nothing to review.
    path = snap / "review.jsonl"
    write_jsonl(path, out)
    print(f"{len(out)} records across {len({r['pmid'] for r in out})} papers -> {path}")
    if a.tsv:
        # An export for a human to read in a spreadsheet, produced on request.
        # Tabs and newlines are rejected rather than escaped: the measurement
        # that justified TSV covered extraction fields only, and the challenge
        # adds three free-text ones.
        tsv = Path(a.tsv)
        with tsv.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, delimiter="\t",
                                extrasaction="raise",
                                quoting=_csv.QUOTE_NONE, escapechar="\\")
            w.writeheader()
            w.writerows(out)
        print(f"  exported {tsv}")
    if missed:
        print(f"{len(missed)} grounded missed-variant reports across {len({p for p, _ in missed})} papers:")
        for p, m in missed[:10]:
            print(f"  {p}: {m[:70]}")
    return 0


# ---------------------------------------------------------------- ledger

def cmd_ledger(a: argparse.Namespace) -> int:
    """Report the denominator: everything we have ever considered."""
    st: Settings = a.settings
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    if not ledger.rows:
        print(f"no ledger yet at {ledger.path}")
        return 0
    s = ledger.summary()
    print(f"{s['identities']} candidates over {s['events']} events  ({ledger.path})")
    print(f"  kept by the gate: {s['kept_by_gate']}   with records: {s['with_records']}"
          f"   needing review: {s['needs_review']}   wedged: {s['wedged']}")
    print(f"  kept but not extracted: {s['kept_not_extracted']}"
          "   (the gate wanted these and no extraction exists at their current text)")
    print("  by stage:       " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_stage"].items())))
    print("  by disposition: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_disposition"].items())))
    if a.tsv:
        import csv as _csv
        cols = ["identity", "identity_kind", "pmid", "stage", "dropped_at",
                "is_open_access", "has_pmcid", "n_variants", "needs_review", "attempts",
                "last_error", "first_pub_date", "journal", "title"]
        out = Path(a.tsv)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore",
                                quoting=_csv.QUOTE_NONE, escapechar="\\")
            w.writeheader()
            for r in sorted(ledger.fold().values(), key=lambda x: (x.get("stage") or "", x.get("identity"))):
                row = {k: r.get(k, "") for k in cols}
                row["title"] = " ".join(str(row["title"]).split())[:160]
                w.writerow(row)
        print(f"  wrote {out}")
    return 0


# ---------------------------------------------------------------- show

def latest_extractions(path: Path) -> list[dict]:
    """One row per PMID, keeping the newest.

    `extractions.jsonl` is append-only, so a paper re-extracted after its text
    grew (a supplement or figure transcription arriving) has more than one row.
    Readers want the current result, not the superseded one. Error rows are
    dropped: they are a log of why a paper was skipped, not a result.
    """
    by_pmid: dict[str, dict] = {}
    for r in read_jsonl(path):
        if r.get("error"):
            continue
        by_pmid[r["pmid"]] = r
    return list(by_pmid.values())


def cmd_show(a: argparse.Namespace) -> int:
    snap = snapshot_dir(a.snapshot, a.settings)
    rows = latest_extractions(snap / "extractions.jsonl")
    if a.pmid:
        rows = [r for r in rows if r["pmid"] == a.pmid]
    for r in rows:
        ex = r["extraction"]
        g = r["grounding"]
        prov = ", ".join(f"{k}={v}" for k, v in sorted((g.get("region_counts") or {}).items()))
        figs = len(g.get("figure_sourced") or [])
        print(f"\n=== PMID {r['pmid']} ({ex['paper_type']}; {'abstract only' if ex['input_was_abstract_only'] else 'full text'}) "
              f"grounded {g['grounded']}/{g['total']}"
              f"{'  quotes: ' + prov if prov else ''}"
              f"{f'  [{figs} FIGURE-SOURCED]' if figs else ''}"
              f"{' REVIEW' if g['needs_review'] else ''}")
        for v in ex["variants"]:
            ph = v.get("phenotype_systems") or "none reported"
            inh = "; ".join(f"{k.removeprefix('inh_')}×{v[k]}" for k in
                            ("inh_de_novo_confirmed", "inh_de_novo_assumed", "inh_inherited", "inh_unknown")
                            if v.get(k)) or "unknown"
            mark = " [MODEL-TRANSCRIBED]" if v["as_written"] in (g.get("figure_sourced") or []) else ""
            print(f"  {v['gene']} {v['as_written']} ({v.get('hgvs_c') or '-'} / {v.get('hgvs_p') or '-'}) type={v['variant_type']} "
                  f"probands={v['n_probands']} families={v['n_families']}"
                  f"{'/' + str(v['n_tested']) + ' tested' if v.get('n_tested') else ''} "
                  f"inh={inh} conf={v['confidence']}{mark}")
            print(f"    {ph}")
            if v.get("notes"):
                print(f"    notes: {v['notes']}")
        if ex.get("extraction_notes"):
            print(f"  paper notes: {ex['extraction_notes']}")
    return 0


# ---------------------------------------------------------------- list

def cmd_list(a: argparse.Namespace) -> int:
    snap = snapshot_dir(a.snapshot, a.settings)
    for r in read_jsonl(snap / "papers.jsonl"):
        tr = r.get("triage", {})
        flags = "".join(["T" if tr.get("title_abstract_mention") else "-", "C" if tr.get("clinical_keyword") else "-",
                         "P" if tr.get("pubtator_gene_variant") else "-", "S" if tr.get("pubtator_search_hit") else "-",
                         "V" if tr.get("pubtator_gene_linked_variant") else "-",
                         "L" if tr.get("llm_keep") else ("-" if "llm_keep" in tr else "?")])
        print(f"{r['pmid']:>9} {r.get('pmcid') or '-':>12} {r.get('first_pub_date') or '':>10} "
              f"{'FT' if not r['abstract_only'] else 'AB'} {flags} {'keep' if tr.get('keep') else 'skip'}  {r['title'][:78]}")
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="col4a1a2", description="COL4A1/COL4A2 literature watcher")
    # Carried by every subcommand via parents=[common]. Deliberately NOT also on
    # the top-level parser: two parsers writing one dest means the later write
    # silently wins. The consequence is that these flags go *after* the
    # subcommand -- `col4a1a2 extract --config X`, not `col4a1a2 --config X extract`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to col4a1a2.toml (default config/col4a1a2.toml)")
    common.add_argument("--cache-dir", help="override the cache directory from config")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch-papers", parents=[common], help="search Europe PMC + PubTator and fetch texts into a snapshot")
    f.add_argument("--snapshot", help="snapshot directory (default data/raw/<today>)")
    f.add_argument("--date-from", help="YYYY-MM-DD")
    f.add_argument("--date-to", help="YYYY-MM-DD")
    f.add_argument("--limit", type=int, help="keep only the N most recent papers")
    f.add_argument("--max-search", type=int, help="cap search results per query (dev)")
    f.add_argument("--oa-only", action="store_true", help="keep only open-access papers with a PMCID")
    f.add_argument("--skip-pubtator", action="store_true")
    f.add_argument("--fetch-all-texts", action="store_true", help="download full text even for triage-rejected papers")
    f.add_argument("--no-supplements", action="store_true",
                   help="skip downloading and parsing supplementary files")
    f.add_argument("--no-figures", action="store_true",
                   help="skip reading figure images with vision")
    f.add_argument("--figures", action="store_true",
                   help="transcribe figures even for papers whose text already names variants")
    f.add_argument("--rediscover", action="store_true",
                   help="re-query every slice, including sealed ones")
    f.add_argument("--no-llm-triage", action="store_true",
                   help="use the keyword triage rule instead of the Haiku gate (offline / no API key)")
    f.set_defaults(func=cmd_fetch_papers)

    e = sub.add_parser("extract", parents=[common], help="run Claude extraction over a snapshot's papers (synchronous)")
    e.add_argument("--snapshot")
    e.add_argument("--pmid", action="append",
                   help="extract only this paper; repeat for several")
    e.add_argument("--limit", type=int)
    e.add_argument("--model", default=None, help="default from config")
    e.add_argument("--effort", default=None,
                   help="low|medium|high|xhigh|max (default from config)")
    e.add_argument("--count-only", action="store_true", help="only count input tokens per paper")
    e.add_argument("--all", action="store_true", help="include papers the triage step rejected")
    e.add_argument("--budget-usd", type=float,
                   help="stop once this much has been spent (may overshoot by one paper, "
                        "since a call's cost is unknown until it returns)")
    e.set_defaults(func=cmd_extract)

    l = sub.add_parser("list", parents=[common], help="list papers in a snapshot with triage flags (T=gene in title/abstract, C=clinical keyword, P=PubTator gene+variant, S=PubTator search hit)")
    l.add_argument("--snapshot")
    l.set_defaults(func=cmd_list)

    c = sub.add_parser("challenge", parents=[common],
                       help="adversarially review a snapshot's extracted records")
    c.add_argument("--snapshot")
    c.add_argument("--pmid")
    c.add_argument("--limit", type=int)
    c.add_argument("--effort", default=None, help="override the record-count-scaled effort")
    c.add_argument("--budget-usd", type=float, help="stop once this much has been spent")
    c.set_defaults(func=cmd_challenge)

    r = sub.add_parser("review", parents=[common], help="emit the human spot-check queue as TSV")
    r.add_argument("--snapshot")
    r.add_argument("--tsv", metavar="PATH",
                   help="also export the queue as TSV for reading in a spreadsheet; "
                        "the JSONL beside it is the artefact")
    r.set_defaults(func=cmd_review)

    g = sub.add_parser("ledger", parents=[common], help="summarise every candidate ever considered")
    g.add_argument("--tsv", help="also write the folded ledger to this TSV")
    g.set_defaults(func=cmd_ledger)

    s = sub.add_parser("show", parents=[common], help="print extractions in a snapshot")
    s.add_argument("--snapshot")
    s.add_argument("--pmid")
    s.set_defaults(func=cmd_show)

    a = ap.parse_args(argv)
    try:
        a.settings = load_settings(a.config, cache_dir=a.cache_dir)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
