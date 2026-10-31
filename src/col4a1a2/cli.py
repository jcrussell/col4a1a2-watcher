"""Command-line interface: fetch-papers, extract, show."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import anthropic

from col4a1a2.config import REPO, ConfigError, Settings, load_settings
from col4a1a2.discovery import DiscoveryLog, plan as plan_slices
from col4a1a2.extract.prompt import PaperInput
from col4a1a2.extract import rows as rows_mod
from col4a1a2.extract.challenge import records_digest
from col4a1a2.digest import sha256_text
from col4a1a2.extract.llm import Policy, ResponseArchive, SpendMeter
from col4a1a2.extract.triage import TRIAGE_PROMPT_VERSION, TriageCache, keyword_signals, triage_paper
from col4a1a2 import jsonl, schemas
from col4a1a2.http import PoliteClient
from col4a1a2.ledger import Ledger
from col4a1a2.sources import europepmc, ncbi, pubtator
from col4a1a2.sources.europepmc import citation
from col4a1a2.sources.fulltext import TextRegion, jats_inventory, jats_to_text

log = logging.getLogger("col4a1a2")


def make_client(settings: Settings) -> PoliteClient:
    return PoliteClient(
        user_agent=settings.user_agent,
        cache_path=settings.http_cache,
        rates=settings.rates,
    )


def make_anthropic(settings: Settings) -> anthropic.Anthropic:
    """The one place an Anthropic client is constructed.

    The SDK defaults to two retries with 0.5s/8s backoff. Worst case per call
    is timeout x (retries + 1), so both come from config and stay bounded.
    """
    return anthropic.Anthropic(
        max_retries=settings.anthropic_max_retries,
        timeout=settings.anthropic_timeout_s,
    )


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def require_api_key() -> bool:
    """For commands that cannot run without Claude: say so and let the caller exit 2."""
    if has_api_key():
        return True
    print("ANTHROPIC_API_KEY is not set; export it and re-run.", file=sys.stderr)
    return False


def select_pmids(items: list, pmids: list[str] | None, *, key=lambda x: x["pmid"]) -> list:
    """Keep only the named papers, and say which names matched nothing.

    Piloting a prompt change against papers whose answers are known is how this
    project checks a change before paying for the corpus; a mistyped PMID that
    silently matches nothing makes the pilot look clean.
    """
    if not pmids:
        return items
    wanted = set(pmids)
    kept = [x for x in items if key(x) in wanted]
    missing = wanted - {key(x) for x in kept}
    if missing:
        print(f"not in this snapshot or not current here: {', '.join(sorted(missing))}", file=sys.stderr)
    return kept


def snapshot_dir(arg: str | None, settings: Settings) -> Path:
    d = Path(arg) if arg else settings.snapshot_root / dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    st: Settings,
    want_supplements: bool,
    want_figures: bool,
    force_figures: bool,
    acl=None,
    fig_cache=None,
    fig_llm=None,
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
        bundle = sup.fetch_bundle(client, pmcid, deadline_s=st.bundle_deadline_s)
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
        char_budget = st.supplements_max_chars_per_paper
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
                sheets = tabular.filter_sheets(tabular.parse(m.data, m.ext),
                                               keep_all_below=st.supplements_keep_all_below_rows,
                                               max_rows=st.supplements_max_rows_per_sheet)
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
        chosen, gate = figmod.select_figures(figs, probe.as_prompt_text(), force=force_figures,
                                             max_figures=st.figures_max_per_paper)
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
            cached = fig_cache.get(digest, fig_llm.model, refresh=fig_llm.refresh)
            if cached is None:
                try:
                    tr, usage, prov = figmod.transcribe_figure(acl, member.data, mt, fig,
                                                               image_sha256=digest,
                                                               max_tokens=st.figures_max_tokens,
                                                               **fig_llm.kwargs())
                    cached = fig_cache.put(pmcid=pmcid, fig=fig, image_sha256=digest,
                                           transcription=tr, usage=usage, provenance=prov)
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
            # Write point 1. These get no snapshot row and no triage decision,
            # so the ledger is their only record. Identity falls back to DOI,
            # then to a title hash.
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
            # so it is never recorded as closed access on no evidence.
            r = pubtator_hits[pmid]
            by_pmid[pmid] = europepmc.PaperMeta(pmid=pmid, pmcid=None, doi=r.get("doi"), title=r.get("title", ""),
                                                journal=r.get("journal"), pub_year=None,
                                                first_pub_date=(r.get("date") or "")[:10] or None,
                                                is_open_access=False, abstract=None, source="pubtator",
                                                metadata_source="pubtator_only")
    n_lookup_requested, n_lookup_resolved = len(missing), len(resolved)

    metas = list(by_pmid.values())
    # Captured before the filter reassigns `metas`, so the manifest reports a
    # pre-filter number under this name.
    n_candidates_before_filters = len(metas)
    if a.oa_only:
        kept_oa = [m for m in metas if m.is_open_access and m.pmcid]
        # Write point 2. The filter runs before the triage loop, so a paper cut
        # here never gets a decision row; this is its only trace.
        # Partitioned by object identity: `m not in kept_oa` compares dataclass
        # fields down a list, which is quadratic over a pool of thousands.
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
    want_annots = [m.pmid for m in metas if m.pmid] if not a.skip_pubtator else []
    n_pubtator_requested = len(want_annots)
    annots = pubtator.export_biocjson(client, want_annots) if want_annots else {}
    gene_ids = set(st.pubtator_gene_ids)

    # The LLM gate runs on title + abstract only, before any full text is fetched.
    # Without a key (or with --no-llm-triage) we fall back to the keyword rule,
    # which measures the same recall but keeps 3x the noise (docs/measurements.md).
    use_llm = not a.no_llm_triage and has_api_key()
    if not use_llm and not a.no_llm_triage:
        log.warning("ANTHROPIC_API_KEY not set; falling back to keyword triage")
    tcache = TriageCache(st.triage_cache)
    acl = make_anthropic(st) if use_llm else None
    archive = ResponseArchive(st.responses_dir)
    triage_llm = Policy(st.triage_model, st.triage_effort, archive, refresh=a.no_llm_cache)
    fig_llm = Policy(st.figure_model, st.figure_effort, archive, refresh=a.no_llm_cache)

    triaged: list[tuple[europepmc.PaperMeta, list[dict], dict]] = []
    for m in metas:
        doc = annots.get(m.pmid, {})
        hints = pubtator.annotations(doc) if m.pmid in annots else []
        linked = pubtator.gene_linked_variants(doc, gene_ids) if doc else []
        tr = keyword_signals(m.title, m.abstract, hints=hints, linked=linked,
                             search_hit=m.pmid in pubtator_hits, gene_ids=gene_ids)
        if use_llm:
            cached = tcache.get(m.pmid, triage_llm.model, refresh=triage_llm.refresh)
            if cached is None:
                try:
                    decision, usage, prov = triage_paper(acl, m.title, m.abstract,
                                                         **triage_llm.kwargs())
                    # Data before index: the verdict is cached before the ledger records it.
                    cached = tcache.put(m.pmid, decision, usage, prov)
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
            tr["keep"] = tr["keyword_keep"]
        triaged.append((m, hints, tr))

    triaged.sort(key=lambda x: (x[2]["keep"], x[2]["pubtator_gene_linked_variant"],
                                x[2]["pubtator_search_hit"], x[0].first_pub_date or ""), reverse=True)
    n_keep_all = sum(1 for _, _, tr in triaged if tr["keep"])
    log.info("triage: %d of %d papers kept (%s); cache hits %d, new calls %d",
             n_keep_all, len(metas), "llm" if use_llm else "keyword", tcache.hits, tcache.misses)
    # Write point 3. Recorded for every triaged candidate, including the ones
    # `--limit` is about to discard, so each carries its title, journal, date
    # and identifier rather than a bare triage decision.
    for m, _hints, tr in triaged:
        # No disposition: a paper `--limit` did not reach this run is still in
        # play, and recording otherwise would let a later, narrower run bury
        # work already done. The per-run count goes in the manifest instead, and
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
    want_figures = not a.no_figures and has_api_key()
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
                        st=st, want_supplements=want_supplements, want_figures=want_figures,
                        force_figures=a.figures, acl=acl, fig_cache=fig_cache,
                        fig_llm=fig_llm,
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
            "text_sha256": sha256_text(text),
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
    n = jsonl.write(snap / "papers.jsonl", rows)
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
        # Requested against resolved: the fallback fills in metadata for every
        # miss, so a lookup that quietly resolves almost nothing is visible only
        # here. A drop in the ratio is the first sign of a break.
        "pmid_lookup_requested": n_lookup_requested,
        "pmid_lookup_resolved": n_lookup_resolved,
        "pubtator_export_requested": n_pubtator_requested,
        "pubtator_export_resolved": len(annots),
        # Anything that changes the assembled text changes the resume key, so
        # the manifest is where a reader finds out why papers were re-extracted.
        "text_affecting": st.text_affecting(),
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
        for r in jsonl.read(snap / "papers.jsonl")
        if r.get("triage", {}).get("keep", True) or getattr(_paper_inputs, "include_all", False)
    ]


def cmd_extract(a: argparse.Namespace) -> int:
    from col4a1a2.extract.client import (
        ExtractionRefused,
        ExtractionTruncated,
        count_tokens,
        extract_paper,
    )
    from pydantic import ValidationError as PydanticValidationError

    from col4a1a2.extract.prompt import PROMPT_VERSION
    from col4a1a2.extract.verify import ground

    if not require_api_key():
        return 2
    st: Settings = a.settings
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    # argparse defaults are None so config can win; the CLI flag still beats it.
    model = a.model or st.extract_model
    effort = a.effort or st.extract_effort
    # The ledger holds one extraction state per paper, so only the configured
    # model writes to it. A comparison run with `--model X` writes its rows to
    # the snapshot beside the current ones and leaves the ledger alone.
    is_current = model == st.extract_model
    archive = ResponseArchive(st.responses_dir)
    snap = snapshot_dir(a.snapshot, st)
    _paper_inputs.include_all = a.all
    papers = _paper_inputs(snap)
    # Named papers only, for piloting a prompt or schema change against papers
    # whose previous answers and challenge verdicts are known.
    papers = select_pmids(papers, a.pmid, key=lambda p: p.pmid)
    if a.limit:
        papers = papers[: a.limit]
    client = make_anthropic(st)
    out_path = st.extractions
    # Resume on (prompt version, text hash), so a paper whose text has grown --
    # supplements or figure transcriptions appended -- is extracted again rather
    # than skipped.
    # Rows recording a failure are a log, not a result: including them would mark
    # a transiently refused paper done forever.
    # The requested model is part of the key, so `--model X` extracts alongside
    # the configured model's rows instead of finding them and skipping.
    done = {(r["pmid"], r["text_sha256"]) for r in jsonl.read(out_path)
            if rows_mod.is_current_extraction(r, model)}
    # Resume across snapshots, not just within one, so a paper extracted in
    # last month's snapshot is not re-extracted in this month's at full price,
    # and a large backlog can be split into affordable runs. The ledger is the cross-snapshot index, and it tracks
    # the configured model only (see `is_current` above); `needs_extraction`
    # is the single statement of the rule.
    if a.no_llm_cache:
        done = set()
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
    meter = SpendMeter(st.price_for(model), archive, a.budget_usd)
    for p in papers:
        text_hash = sha256_text(p.text)
        if (p.pmid, text_hash) in done or (
                is_current and not a.no_llm_cache
                and not ledger.needs_extraction(p.pmid, text_hash, PROMPT_VERSION, model)):
            log.info("skip %s (already extracted at %s on identical text)", p.pmid, PROMPT_VERSION)
            n_skipped += 1
            continue
        if meter.over_budget():
            log.warning("budget of $%.2f reached after %d papers; stopping", a.budget_usd, n_ok)
            break
        log.info("extracting %s (%s)", p.pmid, "abstract" if p.abstract_only else "full text")
        meter.begin()
        try:
            res = extract_paper(client, p, model=model, effort=effort, text_sha256=text_hash,
                                archive=archive, refresh=a.no_llm_cache)
        except (ExtractionRefused, ExtractionTruncated, PydanticValidationError) as e:
            # One unusable paper must not abort the batch; record why and move on.
            log.warning("%s: %s", p.pmid, e)
            jsonl.append(out_path, {"pmid": p.pmid, "pmcid": p.pmcid, "snapshot": snap.name,
                                    "prompt_version": PROMPT_VERSION,
                                    "requested_model": model, "effort": effort,
                                    "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                    "text_sha256": text_hash, "error": type(e).__name__,
                                    "detail": str(e)})
            n_failed += 1
            if not is_current:
                continue
            ledger.record(identity=p.pmid, stage="fetched", snapshot=snap.name,
                          text_sha256=text_hash, prompt_version=PROMPT_VERSION,
                          last_error=type(e).__name__,
                          requested_model=model,
                          attempts=ledger.failures(p.pmid, text_hash, PROMPT_VERSION, model) + 1)
            continue
        rep = ground(res.extraction, texts[p.pmid], regions_by_pmid.get(p.pmid, []))
        reply = res.reply
        row = {
            "pmid": p.pmid, "pmcid": p.pmcid, **reply.provenance(), "snapshot": snap.name,
            "prompt_version": res.prompt_version,
            "text_sha256": text_hash,
            "schema_version": res.schema_version, "stop_reason": reply.stop_reason,
            "usage": reply.usage_row(),
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
        # Data before index: the result row lands before the ledger event, so
        # a crash between them leaves an unindexed result (harmless, reported
        # by `check`) and never an index entry pointing at nothing.
        jsonl.append(out_path, row)
        ex = res.extraction
        n_ok += 1
        meter.charge(reply.usage)
        if is_current:
            ledger.record(identity=p.pmid, stage="extracted", snapshot=snap.name,
                          text_sha256=text_hash, prompt_version=res.prompt_version,
                          requested_model=reply.requested_model,
                          n_variants=len(ex.variants), n_grounded=rep.grounded_quotes,
                          n_ungrounded=len(rep.ungrounded), needs_review=rep.needs_review,
                          n_figure_sourced=len(rep.figure_sourced),
                          last_error=None, attempts=0)
        print(f"{p.pmid}: {ex.paper_type.value}, variants={len(ex.variants)}, "
              f"quotes grounded {rep.grounded_quotes}/{rep.total_quotes}, "
              f"tokens in={reply.usage['input']} out={reply.usage['output']} "
              f"cache_read={reply.usage['cache_read']}"
              + ("  [REVIEW]" if rep.needs_review else ""))
    print(f"\n{n_ok} extracted, {n_skipped} already done, {n_failed} failed, ${meter.spent:.2f} spent")
    if ledger.appended:
        log.info("ledger: %d events appended", ledger.appended)
    return 0


# ---------------------------------------------------------------- challenge

def cmd_challenge(a: argparse.Namespace) -> int:
    """Adversarially review extracted records, and verify the reviewer's output."""
    import anthropic  # noqa: F401  (imported for its exceptions via make_anthropic)

    from col4a1a2.extract.challenge import ChallengeCache, ChallengeFailed, challenge_paper, check

    if not require_api_key():
        return 2
    st: Settings = a.settings
    snap = snapshot_dir(a.snapshot, st)
    papers = {r["pmid"]: r for r in jsonl.read(snap / "papers.jsonl")}
    rows = rows_mod.latest_extractions(st.extractions, st.extract_model, snapshot=snap.name)
    rows = select_pmids(rows, a.pmid)
    # Every extracted paper, including those with no records: a zero-record
    # extraction is either an empty paper or a total miss, and only
    # missed_variants distinguishes them.
    rows = [r for r in rows if r["pmid"] in papers]
    if a.limit:
        rows = rows[: a.limit]

    cache = ChallengeCache(st.curated_dir / "challenges.jsonl")
    client = make_anthropic(st)
    ledger = Ledger(st.curated_dir / "ledger.jsonl")
    archive = ResponseArchive(st.responses_dir)
    # The reviewer runs on the extraction model, and reviews only current rows.
    model = st.extract_model
    meter = SpendMeter(st.price_for(model), archive, a.budget_usd)
    n_done = n_failed = 0

    for r in rows:
        pmid = r["pmid"]
        paper = papers[pmid]
        text = paper["text"]
        # The reviewer grounds against the text the records were extracted
        # from. If the paper's text has grown since, the records are stale:
        # re-extract first, then challenge what that produces.
        if paper.get("text_sha256") != r["text_sha256"]:
            log.warning("skip %s: text changed since extraction; re-extract before challenging", pmid)
            continue
        recs = rows_mod.review_records(r)
        digest = records_digest(recs)
        cached = cache.get(r["text_sha256"], digest, model, refresh=a.no_llm_cache)
        if cached is not None:
            log.info("skip %s (challenged at %s on identical text and records)", pmid,
                     cached.get("challenge_prompt_version"))
            continue
        if meter.over_budget():
            log.warning("budget of $%.2f reached after %d papers; stopping", a.budget_usd, n_done)
            break
        log.info("challenging %s (%d records)", pmid, len(recs))
        meter.begin()
        try:
            ch, usage, prov = challenge_paper(client, pmid=pmid, source_text=text, records=recs,
                                              model=model, effort=a.effort,
                                              archive=archive, refresh=a.no_llm_cache)
        except (ChallengeFailed, Exception) as e:  # fails closed: never "nothing to refute"
            if isinstance(e, KeyboardInterrupt):
                raise
            log.warning("%s: challenge failed (%s: %s)", pmid, type(e).__name__, e)
            n_failed += 1
            continue
        rep = check(ch, recs, text)
        # Data before index, as in `cmd_extract`.
        cache.put(pmid=pmid, text_sha256=r["text_sha256"], records_digest=digest,
                  challenge=ch, report=rep, usage=usage, provenance=prov)
        meter.charge(usage)
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
              f"{'  ' + ' '.join(flags) if flags else ''}  [{prov['effort']}]")

    print(f"\n{n_done} challenged, {n_failed} failed, cache hits {cache.hits}, ${meter.spent:.2f} spent")
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
    """Write the human spot-check queue as JSONL, from both review signals; TSV on request."""
    st: Settings = a.settings
    snap = snapshot_dir(a.snapshot, st)
    papers = {r["pmid"]: r for r in jsonl.read(snap / "papers.jsonl")}
    rows = rows_mod.latest_extractions(st.extractions, st.extract_model, snapshot=snap.name)

    from col4a1a2.extract.challenge import record_key
    current = rows_mod.current_challenges(
        jsonl.read_tolerant(st.curated_dir / "challenges.jsonl", what="challenges"), st.extract_model)
    # The challenge that reviewed these records on this text, or none: matching
    # on the PMID alone would attach verdicts about superseded records.
    challenges = {r["pmid"]: c for r in rows if (c := rows_mod.challenge_for(r, current))}

    def clean(v) -> str:
        # Challenge output is free text that may contain separators, so
        # collapse them rather than assume.
        return " ".join(str(v or "").split()).replace("\t", " ")

    out = []
    for r in rows:
        pmid = r["pmid"]
        g = r.get("grounding") or {}
        ch = challenges.get(pmid, {})
        verdicts = {v["record_key"]: v for v in (ch.get("challenge") or {}).get("verdicts", [])}
        for i, v in enumerate(r["extraction"].get("variants") or []):
            key = record_key(pmid, i, v)
            _verdict, reasons = rows_mod.record_review(key, v, g, ch or None)
            vd = verdicts.get(key, {})
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
    # would attribute corpus-wide counts to one snapshot.
    in_snapshot = {r["pmid"] for r in rows}
    missed = [(p, m) for p, c in challenges.items() if p in in_snapshot
              for m in (c.get("report") or {}).get("missed_grounded", [])]
    # JSONL is the artefact; it has a schema and nothing downstream parses TSV.
    # Written even when empty -- a curator finding an empty queue and a curator
    # finding no file are different situations.
    path = snap / "review.jsonl"
    jsonl.write(path, out)
    print(f"{len(out)} records across {len({r['pmid'] for r in out})} papers -> {path}")
    if a.tsv:
        # An export for a human to read in a spreadsheet, produced on request.
        jsonl.write_table(Path(a.tsv), REVIEW_COLUMNS, out, delimiter="\t")
        print(f"  exported {a.tsv}")
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
        cols = ["identity", "identity_kind", "pmid", "stage", "dropped_at",
                "is_open_access", "has_pmcid", "n_variants", "needs_review", "attempts",
                "last_error", "first_pub_date", "journal", "title"]
        rows = sorted(ledger.fold().values(), key=lambda x: (x.get("stage") or "", x.get("identity")))
        jsonl.write_table(Path(a.tsv), cols, delimiter="\t", rows=({**r, "title": str(r.get("title") or "")[:160]} for r in rows))
        print(f"  wrote {a.tsv}")
    return 0


# ---------------------------------------------------------------- show

def cmd_publish(a: argparse.Namespace) -> int:
    """Build the release from tracked files: evidence and literature tables, datapackage, site."""
    from col4a1a2 import publish

    rel = publish.gather(a.settings, include_all=a.all)
    out = Path(a.out) if a.out else REPO / "_site"
    for path in publish.write(rel, out):
        print(f"  wrote {path}")
    print(f"{len(rel.evidence)} evidence rows ({'all' if rel.include_all else 'confident'} of "
          f"{rel.n_extracted} extracted), {len(rel.literature)} papers considered, data as of {rel.version}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    from col4a1a2 import check

    return check.run(a.settings, repo=REPO, as_json=a.json)


def cmd_show(a: argparse.Namespace) -> int:
    snap = snapshot_dir(a.snapshot, a.settings)
    rows = rows_mod.latest_extractions(a.settings.extractions, a.settings.extract_model, snapshot=snap.name)
    rows = select_pmids(rows, a.pmid)
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
    for r in jsonl.read(snap / "papers.jsonl"):
        tr = r.get("triage", {})
        flags = "".join(["T" if tr.get("title_abstract_mention") else "-", "C" if tr.get("clinical_keyword") else "-",
                         "P" if tr.get("pubtator_gene_variant") else "-", "S" if tr.get("pubtator_search_hit") else "-",
                         "V" if tr.get("pubtator_gene_linked_variant") else "-",
                         "L" if tr.get("llm_keep") else ("-" if "llm_keep" in tr else "?")])
        print(f"{r['pmid']:>9} {r.get('pmcid') or '-':>12} {r.get('first_pub_date') or '':>10} "
              f"{'FT' if not r['abstract_only'] else 'AB'} {flags} {'keep' if tr.get('keep') else 'skip'}  {r['title'][:78]}")
    return 0


# ---------------------------------------------------------------- main

# Verbs that write. They take `Settings.lock_file` so two runs never interleave
# appends or race on a resume decision, and so `publish` never reads a log
# another run is halfway through appending; read-only verbs skip it.
WRITING_VERBS = frozenset({"fetch-papers", "extract", "challenge", "review", "publish"})


class LockHeld(RuntimeError):
    pass


def hold_lock(path: Path):
    """Take an exclusive, non-blocking lock and write our PID into the file.

    The OS drops the lock when the process dies, however it dies, so there is
    no stale-lock cleanup. Returns the open file, which must stay referenced
    for as long as the lock is needed.
    """
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip() or "unknown"
        f.close()
        raise LockHeld(f"another col4a1a2 run (pid {holder}) is writing to {path.parent}; "
                       "wait for it to finish") from None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


# Named for the LLM so nobody reads it as "ignore the HTTP cache": that would
# re-download every article, which `http.py` exists to prevent.
NO_LLM_CACHE_HELP = ("call Claude even when an answer is cached or archived; the new answer "
                     "is appended and becomes the current row (costs money)")


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
    # Options shared by several verbs, defined once so their meaning, type and
    # help text cannot drift apart between verbs.
    snap_opt = argparse.ArgumentParser(add_help=False)
    snap_opt.add_argument("--snapshot", help="snapshot directory (default: the configured snapshot root/<today>)")
    pick_opt = argparse.ArgumentParser(add_help=False)
    pick_opt.add_argument("--pmid", action="append", help="only this paper; repeat for several")
    pick_opt.add_argument("--limit", type=int, help="process at most this many papers")
    fresh_opt = argparse.ArgumentParser(add_help=False)
    fresh_opt.add_argument("--no-llm-cache", action="store_true", help=NO_LLM_CACHE_HELP)
    budget_opt = argparse.ArgumentParser(add_help=False)
    budget_opt.add_argument("--budget-usd", type=float,
                            help="stop once this much has been spent (may overshoot by one paper, "
                                 "since a call's cost is unknown until it returns)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch-papers", parents=[common, snap_opt, fresh_opt],
                       help="search Europe PMC + PubTator and fetch texts into a snapshot")
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

    e = sub.add_parser("extract", parents=[common, snap_opt, pick_opt, fresh_opt, budget_opt],
                       help="run Claude extraction over a snapshot's papers (synchronous)")
    e.add_argument("--model", default=None, help="default from config")
    e.add_argument("--effort", default=None,
                   help="low|medium|high|xhigh|max (default from config)")
    e.add_argument("--count-only", action="store_true", help="only count input tokens per paper")
    e.add_argument("--all", action="store_true", help="include papers the triage step rejected")
    e.set_defaults(func=cmd_extract)

    ls = sub.add_parser("list", parents=[common, snap_opt], help="list papers in a snapshot with triage flags (T=gene in title/abstract, C=clinical keyword, P=PubTator gene+variant, S=PubTator search hit)")
    ls.set_defaults(func=cmd_list)

    c = sub.add_parser("challenge", parents=[common, snap_opt, pick_opt, fresh_opt, budget_opt],
                       help="adversarially review a snapshot's extracted records")
    c.add_argument("--effort", default=None, help="override the record-count-scaled effort")
    c.set_defaults(func=cmd_challenge)

    r = sub.add_parser("review", parents=[common, snap_opt], help="emit the human spot-check queue")
    r.add_argument("--tsv", metavar="PATH",
                   help="also export the queue as TSV for reading in a spreadsheet; "
                        "the JSONL beside it is the artefact")
    r.set_defaults(func=cmd_review)

    g = sub.add_parser("ledger", parents=[common], help="summarise every candidate ever considered")
    g.add_argument("--tsv", help="also write the folded ledger to this TSV")
    g.set_defaults(func=cmd_ledger)

    pb = sub.add_parser("publish", parents=[common],
                        help="build the public tables and site from tracked files (what CI runs)")
    pb.add_argument("--out", help="output directory (default _site/ in the repository)")
    pb.add_argument("--all", action="store_true",
                    help="release every current record, not only confident ones; for inspection, never CI")
    pb.set_defaults(func=cmd_publish)

    k = sub.add_parser("check", parents=[common],
                       help="verify keys, foreign keys and append-only logs (run by the pre-commit hook)")
    k.add_argument("--json", action="store_true", help="one finding per line as JSON")
    k.set_defaults(func=cmd_check)

    s = sub.add_parser("show", parents=[common, snap_opt], help="print extractions in a snapshot")
    s.add_argument("--pmid", action="append", help="only this paper; repeat for several")
    s.set_defaults(func=cmd_show)

    a = ap.parse_args(argv)
    try:
        a.settings = load_settings(a.config, cache_dir=a.cache_dir)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if a.cmd in WRITING_VERBS:
        try:
            lock = hold_lock(a.settings.lock_file)  # noqa: F841  (held until return)
        except LockHeld as e:
            print(str(e), file=sys.stderr)
            return 3
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
