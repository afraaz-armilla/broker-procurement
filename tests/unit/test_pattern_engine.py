from bpb.enrichment.pattern_engine import generate_candidate, learn_pattern, render_pattern


def test_render_pattern_basic_templates():
    assert render_pattern("{first}.{last}", "Jane", "Doe") == "jane.doe"
    assert render_pattern("{f}{last}", "Jane", "Doe") == "jdoe"
    assert render_pattern("{first}", "Jane", "Doe") == "jane"


def test_render_pattern_strips_punctuation_and_case():
    assert render_pattern("{first}.{last}", "O'Brien", "Smith-Jones") == "obrien.smithjones"


def test_generate_candidate_from_known_pattern():
    candidate = generate_candidate("Jane", "Doe", "example.com", pattern="{first}.{last}")
    assert candidate == "jane.doe@example.com"


def test_learn_pattern_needs_at_least_min_votes():
    emails = [("Jane", "Doe", "jane.doe@example.com")]
    assert learn_pattern(emails, min_votes=2) is None


def test_learn_pattern_infers_dominant_template():
    emails = [
        ("Jane", "Doe", "jane.doe@example.com"),
        ("John", "Smith", "john.smith@example.com"),
        ("Amy", "Lee", "amy.lee@example.com"),
    ]
    assert learn_pattern(emails) == "{first}.{last}"


def test_learn_pattern_returns_none_when_no_template_matches():
    emails = [("Jane", "Doe", "totally-different@example.com")]
    assert learn_pattern(emails, min_votes=1) is None


def test_learn_pattern_picks_majority_over_minority():
    emails = [
        ("Jane", "Doe", "jdoe@example.com"),
        ("John", "Smith", "jsmith@example.com"),
        ("Amy", "Lee", "amy.lee@example.com"),  # minority pattern
    ]
    assert learn_pattern(emails, min_votes=2) == "{f}{last}"
