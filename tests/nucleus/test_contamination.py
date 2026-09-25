"""arail.nucleus.evals.contamination (T-CONT-1..5)."""

from __future__ import annotations

from arail.nucleus.evals import contamination as cm

CUTOFF = "2026-06-01"


def _cert_item(i, text):
    return {"id": f"cert-{i}", "date": "2026-07-01", "text": text}


LONG_TEXT = " ".join(f"word{i}" for i in range(30))


def test_normalize_lowercases_and_splits_on_nonword():
    tokens = cm.normalize("Hello, World!  Foo-Bar")
    assert tokens == ["hello", "world", "foo", "bar"]


# ── T-CONT-1/2: overlap ratio thresholds (10/800=0.0125 refuse, 7/800=0.00875 pass) ──

def test_overlap_above_one_percent_flagged():
    cert_items = [_cert_item(i, f"unique cert text number {i} " + LONG_TEXT) for i in range(800)]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    # Exact-duplicate 10 of the 800 (distinct) cert texts into train -> overlap = 10/800 = 0.0125.
    for i in range(10):
        checker.observe_train_doc(f"unique cert text number {i} " + LONG_TEXT, date="2026-01-01")
    report = checker.check()
    assert report.overlap == 10 / 800
    assert report.contaminated is True


def test_overlap_below_one_percent_not_flagged():
    cert_items = [_cert_item(i, f"unique cert text number {i} " + LONG_TEXT) for i in range(800)]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    for i in range(7):
        checker.observe_train_doc(f"unique cert text number {i} " + LONG_TEXT, date="2026-01-01")
    report = checker.check()
    assert report.overlap == 7 / 800
    assert report.contaminated is False


# ── T-CONT-3: exact-sha duplicate counted ─────────────────────────────

def test_exact_sha_duplicate_counts_as_contaminated():
    cert_items = [_cert_item(0, "unique text A"), _cert_item(1, "unique text B different words entirely here")]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    checker.observe_train_doc("unique text A", date="2026-01-01")  # exact dup of cert-0
    report = checker.check()
    assert "cert-0" in report.top_offenders
    assert "cert-1" not in report.top_offenders


# ── T-CONT-4: train item dated after cutoff -> temporal_leak ─────────

def test_temporal_leak_detected():
    cert_items = [_cert_item(0, "x")]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    checker.observe_train_doc("some train text", date="2026-08-01")  # after cutoff
    report = checker.check()
    assert report.temporal_leak == "2026-08-01"
    assert report.contaminated is True


def test_no_temporal_leak_when_all_train_before_cutoff():
    cert_items = [_cert_item(0, "x")]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    checker.observe_train_doc("train text", date="2026-01-01")
    report = checker.check()
    assert report.temporal_leak == "none"


# ── T-CONT-5: shared boilerplate n-grams don't trigger ────────────────

def test_boilerplate_header_does_not_trigger_contamination():
    boilerplate = "Signed-off-by: Fixture Author fixture@example.invalid " * 2
    cert_items = [_cert_item(i, boilerplate + f" unique cert body {i} more words to pad this out nicely")
                 for i in range(20)]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    # Every train doc shares the same boilerplate header (>5% doc freq) but
    # has otherwise unrelated body text.
    for i in range(20):
        checker.observe_train_doc(boilerplate + f" totally different train body {i} padding words here too",
                                  date="2026-01-01")
    report = checker.check()
    assert report.overlap == 0.0
    assert report.params["n_train_docs"] == 20


def test_check_convenience_wrapper():
    cert_items = [_cert_item(0, "abc")]
    report = cm.check(cert_items, [("abc", "2026-01-01")], cutoff=CUTOFF)
    assert report.overlap == 1.0


def test_top_offenders_capped_at_10():
    cert_items = [_cert_item(i, f"dup text {i}") for i in range(15)]
    checker = cm.ContaminationChecker(cert_items, cutoff=CUTOFF)
    for i in range(15):
        checker.observe_train_doc(f"dup text {i}", date="2026-01-01")
    report = checker.check()
    assert len(report.top_offenders) == 10
