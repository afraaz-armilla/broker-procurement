from bpb.gates.sanctions.matcher import (
    SanctionsEntry,
    build_index,
    normalize_name,
    screen_name,
)

FIXTURE_ENTRIES = [
    SanctionsEntry(
        list_source="ofac_sdn", name="Viktor Alexandrovich Petrov", program="RUSSIA-EO14024"
    ),
    SanctionsEntry(list_source="ofac_sdn", name="Sanctioned Holdings Inc", entity_type="entity"),
    SanctionsEntry(list_source="uksl", name="Maria Elena Rodriguez Santos", program="LIBYA"),
    SanctionsEntry(
        list_source="un_consolidated",
        name="Ahmed Al-Rashid",
        program="ISIL (Da'esh) and Al-Qaida",
    ),
]


def index():
    return build_index(FIXTURE_ENTRIES)


def test_normalize_name_strips_punctuation_diacritics_and_legal_suffixes():
    assert normalize_name("Petrov, Viktor A.") == "petrov viktor a"
    assert normalize_name("Sanctioned Holdings, Inc.") == "sanctioned holdings"
    assert normalize_name("Ähmed Al-Rashïd") == "ahmed al rashid"


def test_normalize_name_preserves_group_and_holdings():
    # "Group"/"Holdings" are often the distinguishing part of an entity's identity
    # (two differently-sanctioned entities under one parent brand) — stripping them
    # would risk collapsing distinct entities into a false exact match.
    assert normalize_name("Acme Group") != normalize_name("Acme")
    assert normalize_name("Acme Holdings") != normalize_name("Acme Trading")


def test_exact_normalized_match_is_a_hit():
    result = screen_name("Viktor Alexandrovich Petrov", index())
    assert result.verdict == "match"
    assert result.best_score == 100.0
    assert result.matched_entry_name == "Viktor Alexandrovich Petrov"


def test_exact_match_is_order_and_case_insensitive():
    result = screen_name("petrov viktor alexandrovich", index())
    assert result.verdict == "match"


def test_entity_name_with_corporate_suffix_still_matches():
    result = screen_name("Sanctioned Holdings, Inc.", index())
    assert result.verdict == "match"


def test_clearly_unrelated_name_is_clear():
    result = screen_name("Jane Q. Broker", index())
    assert result.verdict == "clear"


def test_common_western_name_does_not_false_positive_on_single_token_overlap():
    # "James Wilson" shares no strong overlap with any fixture entry — should be clear,
    # not a potential_match purely because "James" or "Wilson" fuzzily resembles something.
    result = screen_name("James Wilson", index())
    assert result.verdict == "clear"


def test_partial_name_overlap_below_dual_token_threshold_stays_clear():
    # Shares only "Rodriguez" with the UKSL entry — one token isn't enough to flag.
    result = screen_name("Rodriguez Consulting LLC", index())
    assert result.verdict != "match"


def test_partial_subset_name_with_strong_overlap_is_potential_match_not_exact():
    # All three query tokens appear in "Maria Elena Rodriguez Santos" (missing
    # surname "Santos") — token_set_ratio scores a pure subset very highly, and with
    # 3-token overlap this should be flagged for review, but it is NOT a normalized
    # exact match (the token sets differ), so it must not silently auto-disqualify
    # as a hard `match` — a human needs to look at it.
    result = screen_name("Maria Elena Rodriguez", index())
    assert result.verdict == "potential_match"
    assert result.matched_entry_name == "Maria Elena Rodriguez Santos"


def test_empty_index_always_clears():
    empty = build_index([])
    result = screen_name("Viktor Petrov", empty)
    assert result.verdict == "clear"


def test_blank_query_clears():
    result = screen_name("   ", index())
    assert result.verdict == "clear"


def test_screening_result_serializes_to_json():
    result = screen_name("Viktor Petrov", index())
    payload = result.model_dump_json()
    assert '"verdict"' in payload
