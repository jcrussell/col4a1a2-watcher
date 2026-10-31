"""Content hashes, defined once.

Resume, the caches, the challenge key and the response archive all key on
sha256 digests, and all compute them here: two spellings of a key are two
chances for them to disagree.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(obj: Any) -> str:
    """Digest of canonical JSON: sorted keys, no ASCII escaping, `str` for the rest."""
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))
