"""Pure name-screening logic — no I/O, no network. This is a name-only watchlist
match, the floor of compliance for a prospecting gate (no DOB/jurisdiction/adverse
media) — see docs/compliance.md for why that's the right scope here.

The public entry points are `build_index` (called once per refresh, from parsed
list entries) and `screen_name` (called once per prospect). Both are plain
functions over plain data, which is what makes this module trivially unit-testable
and independent of the sanctions-refresh/Drive-archive machinery in refresh.py.
"""

from __future__ import annotations

import pickle
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from bpb.models import ScreeningVerdict

MATCHER_VERSION = "1"

CACHE_DIR = Path(".bpb_cache")
INDEX_PATH = CACHE_DIR / "sanctions_index.pkl"

#  Deliberately narrow: only unambiguous legal-form abbreviations. "group" and
#  "holdings" are excluded even though they're common suffixes elsewhere in this
#  codebase (see enrichment/pattern_engine.py) — in a sanctions-entity name they're
#  frequently the meaningful, distinguishing part of the identity (e.g. two
#  differently-sanctioned entities under the same parent brand, "X Holdings" vs
#  "X Trading"), so stripping them risks collapsing two distinct entities into one.
_CORPORATE_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "plc", "gmbh", "sa", "srl", "bv", "ag", "nv",
}


def normalize_name(name: str) -> str:
    """Case-fold, strip diacritics/punctuation, drop common corporate suffixes."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.lower()
    alnum_only = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = [t for t in alnum_only.split() if t and t not in _CORPORATE_SUFFIXES]
    return " ".join(tokens)


@dataclass(frozen=True)
class SanctionsEntry:
    list_source: str
    name: str
    entity_type: str | None = None
    program: str | None = None


@dataclass(frozen=True)
class SanctionsIndex:
    entries: tuple[SanctionsEntry, ...]
    normalized_names: tuple[str, ...]  # parallel to entries
    exact_lookup: dict[frozenset, int] = field(default_factory=dict)
    snapshot_ids: dict[str, str] = field(default_factory=dict)  # list_source -> snapshot id

    def __len__(self) -> int:
        return len(self.entries)


def build_index(
    entries: list[SanctionsEntry], snapshot_ids: dict[str, str] | None = None
) -> SanctionsIndex:
    normalized = tuple(normalize_name(e.name) for e in entries)
    exact_lookup: dict[frozenset, int] = {}
    for i, norm in enumerate(normalized):
        key = frozenset(norm.split())
        exact_lookup.setdefault(key, i)  # first entry with these tokens wins ties
    return SanctionsIndex(
        entries=tuple(entries),
        normalized_names=normalized,
        exact_lookup=exact_lookup,
        snapshot_ids=snapshot_ids or {},
    )


@dataclass(frozen=True)
class ScreeningResult:
    verdict: ScreeningVerdict
    best_score: float
    matched_entry_name: str | None
    matched_lists: list[str]
    matcher_version: str = MATCHER_VERSION

    def model_dump_json(self, **kwargs) -> str:  # duck-typed like a pydantic model, for cli.py
        import json

        return json.dumps(
            {
                "verdict": self.verdict,
                "best_score": self.best_score,
                "matched_entry_name": self.matched_entry_name,
                "matched_lists": self.matched_lists,
                "matcher_version": self.matcher_version,
            },
            indent=kwargs.get("indent"),
        )


_CLEAR = ScreeningResult("clear", 0.0, None, [])


def screen_name(
    name: str, index: SanctionsIndex, *, potential_match_threshold: int = 92
) -> ScreeningResult:
    """Screen one name against the index. See docs/compliance.md for the verdict
    semantics: `match` (normalized-exact) always blocks; `potential_match` (fuzzy,
    with the dual-token overlap guard below) needs human review; `clear` proceeds.

    The dual-token guard: a fuzzy score alone flags "James Wilson" against half the
    SDN list. Requiring at least two overlapping tokens between the query and the
    matched entry (not just the single most distinctive one) is what keeps common
    Western names from generating a potential_match every single week.
    """
    if not index.entries:
        return _CLEAR
    query_norm = normalize_name(name)
    query_tokens = set(query_norm.split())
    if not query_tokens:
        return _CLEAR

    exact_idx = index.exact_lookup.get(frozenset(query_tokens))
    if exact_idx is not None:
        entry = index.entries[exact_idx]
        return ScreeningResult("match", 100.0, entry.name, [entry.list_source])

    match = process.extractOne(query_norm, index.normalized_names, scorer=fuzz.token_set_ratio)
    if match is None:
        return _CLEAR
    matched_norm, score, idx = match
    if score < potential_match_threshold:
        return ScreeningResult("clear", score, None, [])

    entry = index.entries[idx]
    entry_tokens = set(matched_norm.split())
    overlap = query_tokens & entry_tokens
    if len(overlap) >= 2:
        return ScreeningResult("potential_match", score, entry.name, [entry.list_source])
    return ScreeningResult("clear", score, None, [])


def save_index(index: SanctionsIndex, path: Path | None = None) -> None:
    # `path` resolves against the module-level INDEX_PATH at CALL time (a plain
    # global lookup in the function body), not at def time — unlike a mutable
    # default argument, this means tests can monkeypatch matcher.INDEX_PATH and
    # have it take effect without needing to pass `path` explicitly everywhere.
    target = path if path is not None else INDEX_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as f:
        pickle.dump(index, f)


def load_index(path: Path | None = None) -> SanctionsIndex:
    target = path if path is not None else INDEX_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"No sanctions index at {target} — run `bpb refresh-sanctions` first."
        )
    with target.open("rb") as f:
        return pickle.load(f)  # noqa: S301 — trusted, locally-built cache file
