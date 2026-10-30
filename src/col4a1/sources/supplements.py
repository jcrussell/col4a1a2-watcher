"""Supplementary-file and figure-image bundles from Europe PMC.

One request per paper returns a ZIP holding both the article's supplementary
files and its figure image renditions, so a single polite call to a host we
already rate-limit gets everything. Publisher sites are never contacted.

The ZIP is untrusted third-party data whose member names we would otherwise
write straight to disk, so every member is checked for path escape and size
before it is read.
"""

from __future__ import annotations

import json
import logging
import posixpath
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from col4a1.http import DownloadTimeout, PoliteClient, ResponseTooLarge, TooManyFailures
from col4a1.sources.fulltext import FigureRef, SupplementRef

log = logging.getLogger("col4a1")

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# A bundle is not immutable: publishers deposit supplements late, and the cache
# stores any status below 500, so a 404 cached forever would be wrong.
# Bundles are streamed and never put in the HTTP cache: the bodies are large and
# the snapshot directory is the better store. Re-fetch is avoided instead by the
# marker that `write_members` leaves behind.
MARKER = ".fetched"
BUNDLE_DEADLINE_S = 60.0
BUNDLE_READ_TIMEOUT_S = 20.0
# A bundle is optional enrichment, not essential input, and this endpoint fails
# or stalls for roughly half of requests. Retrying it would let those papers
# dominate the wall clock of a whole run, so it gets exactly one attempt and the
# paper is simply recorded without supplements. Measured: two attempts at a 30s
# read timeout cost about 30s per paper on average across a 100-paper batch.
BUNDLE_ATTEMPTS = 1
# Europe PMC builds this zip per request, so it is far more expensive to serve
# than a metadata call. Measured: hammering it at the host's 2/s eventually
# returns HTTP 500. It gets its own bucket, one request every ten seconds.
BUNDLE_RATE_KEY = "europepmc:supplementaryFiles"

MAX_BUNDLE_BYTES = 25 << 20
MAX_MEMBER_BYTES = 8 << 20
MAX_TOTAL_UNCOMPRESSED = 60 << 20

DATA_EXTS = {"xlsx", "xls", "csv", "tsv", "docx", "doc", "pdf", "txt"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "tif", "tiff"}


@dataclass
class BundleMember:
    name: str
    stem: str
    ext: str
    size: int
    data: bytes | None = None
    kind: str = "unmatched"        # figure | supplement | unmatched
    skip_reason: str = ""


@dataclass
class Bundle:
    pmcid: str
    status: int = 0
    n_bytes: int = 0
    members: list[BundleMember] = field(default_factory=list)
    skip_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.skip_reason

    def by_kind(self, kind: str) -> list[BundleMember]:
        return [m for m in self.members if m.kind == kind and not m.skip_reason]

    def find(self, href: str) -> BundleMember | None:
        """Match a JATS href to a member, by exact name then by stem."""
        want = posixpath.basename(href)
        stem = want.rsplit(".", 1)[0] if "." in want else want
        for m in self.members:
            if m.name == want or posixpath.basename(m.name) == want:
                return m
        for m in self.members:
            if m.stem == stem:
                return m
        return None

    def summary(self) -> dict:
        return {
            "status": self.status,
            "bytes": self.n_bytes,
            "members": len(self.members),
            "n_unmatched_members": sum(1 for m in self.members if m.kind == "unmatched"),
            "skip_reason": self.skip_reason,
        }


def bundle_url(pmcid: str) -> str:
    return f"{BASE}/{pmcid}/supplementaryFiles"


def safe_member_name(name: str) -> str | None:
    """The basename of a ZIP member, or None if the name tries to escape.

    Rejects absolute paths, drive letters, parent traversal and empty names.
    We only ever keep the basename, but a name that *attempts* escape is a
    signal worth refusing outright rather than silently sanitising.
    """
    if not name or name.endswith("/"):
        return None
    norm = name.replace("\\", "/")
    if norm.startswith("/") or ".." in norm.split("/") or (len(norm) > 1 and norm[1] == ":"):
        return None
    base = posixpath.basename(norm)
    if not base or base in {".", ".."}:
        return None
    return base


# Measured: Europe PMC answers HEAD on this endpoint with 200 and no
# Content-Length, so a size probe would cost one request per paper and tell us
# nothing. The cap is therefore enforced while streaming instead.

def fetch_bundle(
    client: PoliteClient,
    pmcid: str,
    *,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    deadline_s: float = BUNDLE_DEADLINE_S,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED,
) -> Bundle:
    """Download and open the bundle. Never raises for an absent or unusable bundle."""
    b = Bundle(pmcid=pmcid)
    try:
        # Streamed, not cached: these bodies are large, the snapshot directory is
        # the better store, and at least one bundle stalls mid-transfer, which
        # only a total deadline catches.
        resp = client.download(bundle_url(pmcid), max_bytes=max_bundle_bytes, deadline_s=deadline_s,
                               rate_key=BUNDLE_RATE_KEY, read_timeout=BUNDLE_READ_TIMEOUT_S,
                               max_attempts=BUNDLE_ATTEMPTS)
    except ResponseTooLarge as e:
        b.skip_reason = f"bundle too large: {e}"
        return b
    except DownloadTimeout as e:
        b.skip_reason = f"download stalled: {e}"
        return b
    except TooManyFailures as e:
        b.skip_reason = f"fetch failed: {e}"
        return b
    b.status, b.n_bytes = resp.status, len(resp.body)
    if not resp.ok:
        b.skip_reason = f"HTTP {resp.status}"
        return b
    if b.n_bytes > max_bundle_bytes:
        b.skip_reason = f"bundle too large ({b.n_bytes} bytes > {max_bundle_bytes})"
        return b

    try:
        zf = zipfile.ZipFile(BytesIO(resp.body))
    except zipfile.BadZipFile:
        b.skip_reason = "not a zip archive"
        return b

    total = 0
    # Sorted so two fetches of the same bundle produce the same ordering, which
    # keeps the assembled prompt text deterministic.
    for info in sorted(zf.infolist(), key=lambda i: i.filename):
        base = safe_member_name(info.filename)
        if base is None:
            b.members.append(BundleMember(name=info.filename, stem="", ext="", size=info.file_size,
                                          skip_reason="unsafe member name"))
            continue
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        stem = base.rsplit(".", 1)[0] if "." in base else base
        m = BundleMember(name=base, stem=stem, ext=ext, size=info.file_size)
        if info.file_size > max_member_bytes:
            m.skip_reason = f"member too large ({info.file_size} bytes)"
            b.members.append(m)
            continue
        if total + info.file_size > max_total_uncompressed:
            m.skip_reason = "total uncompressed cap reached"
            b.members.append(m)
            continue
        try:
            m.data = zf.read(info)
        except Exception as e:  # corrupt member; keep the rest of the bundle
            m.skip_reason = f"unreadable member: {type(e).__name__}"
            b.members.append(m)
            continue
        total += len(m.data)
        b.members.append(m)
    return b


def classify(bundle: Bundle, figures: list[FigureRef], supplements: list[SupplementRef]) -> None:
    """Label members as figure renditions or supplements, in place.

    Figure stems come from the article's own JATS, so anything matching one is a
    rendition. Everything else carrying a data extension is a supplement
    candidate, whether or not its href resolved, because some publishers emit
    extension-less hrefs. The remainder stays `unmatched` and is counted rather
    than silently dropped.
    """
    fig_stems = {
        posixpath.basename(h).rsplit(".", 1)[0] if "." in posixpath.basename(h) else posixpath.basename(h)
        for f in figures
        for h in f.graphic_hrefs
    }
    sup_stems = {
        posixpath.basename(h).rsplit(".", 1)[0] if "." in posixpath.basename(h) else posixpath.basename(h)
        for s in supplements
        for h in s.hrefs
    }
    for m in bundle.members:
        if m.skip_reason:
            continue
        if m.stem in fig_stems:
            m.kind = "figure"
        elif m.stem in sup_stems:
            # A supplement the article declares is tracked even when we cannot
            # read it (video, archives, legacy formats), so the reason it was
            # skipped is recorded rather than the file simply vanishing.
            m.kind = "supplement"
        elif m.ext in DATA_EXTS:
            m.kind = "supplement"
        # Anything else keeps the default "unmatched" and is counted, not dropped.


def write_members(bundle: Bundle, dest: Path) -> list[Path]:
    """Persist readable members under `dest`, named by their safe basename.

    Also drops a marker so a later run can tell a completed fetch from an
    interrupted one, and skip re-downloading at the endpoint's deliberately slow
    rate.
    """
    written: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    for m in bundle.members:
        if m.data is None or m.skip_reason:
            continue
        p = dest / m.name
        p.write_bytes(m.data)
        written.append(p)
    (dest / MARKER).write_text(json.dumps(bundle.summary()), encoding="utf-8")
    return written


def load_cached(dest: Path) -> Bundle | None:
    """Rebuild a bundle from a previous run's files, or None if absent.

    The endpoint is rate-limited to one request every ten seconds, so a rerun
    over 200 papers would otherwise spend half an hour re-downloading bytes
    already on disk.
    """
    marker = dest / MARKER
    if not marker.is_file():
        return None
    try:
        summary = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    b = Bundle(pmcid=dest.name, status=int(summary.get("status") or 200),
               n_bytes=int(summary.get("bytes") or 0))
    for p in sorted(dest.iterdir()):
        if p.name == MARKER or not p.is_file():
            continue
        ext = p.suffix.lstrip(".").lower()
        b.members.append(BundleMember(name=p.name, stem=p.stem, ext=ext,
                                      size=p.stat().st_size, data=p.read_bytes()))
    return b
