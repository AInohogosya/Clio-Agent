from ethos.social.verbosity import (
    VERBOSITY_BALANCED,
    VERBOSITY_DETAILED,
    VERBOSITY_QUIET,
    detect_verbosity_request,
    normalise_verbosity,
    verbosity_interval_min,
    verbosity_note,
)


def test_report_more_is_detailed() -> None:
    assert detect_verbosity_request("report more") == VERBOSITY_DETAILED
    assert detect_verbosity_request("Please keep me posted more often") == VERBOSITY_DETAILED
    assert detect_verbosity_request("more detail please") == VERBOSITY_DETAILED


def test_dont_report_so_much_is_quiet() -> None:
    assert detect_verbosity_request("don't report so much") == VERBOSITY_QUIET
    assert detect_verbosity_request("report less, only when done") == VERBOSITY_QUIET
    assert detect_verbosity_request("please stay quiet") == VERBOSITY_QUIET


def test_negation_wins() -> None:
    assert detect_verbosity_request("don't report more, report less") == VERBOSITY_QUIET


def test_no_request() -> None:
    assert detect_verbosity_request("how is the task going?") is None
    assert detect_verbosity_request("") is None


def test_intervals_order() -> None:
    assert verbosity_interval_min(VERBOSITY_DETAILED) < verbosity_interval_min(VERBOSITY_BALANCED)
    assert verbosity_interval_min(VERBOSITY_BALANCED) < verbosity_interval_min(VERBOSITY_QUIET)


def test_notes_are_human_guidance() -> None:
    for level in (VERBOSITY_QUIET, VERBOSITY_BALANCED, VERBOSITY_DETAILED):
        assert verbosity_note(level).strip()


def test_normalise_falls_back_to_balanced() -> None:
    assert normalise_verbosity("loud") == VERBOSITY_BALANCED
