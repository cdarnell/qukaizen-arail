"""Shared nucleus test fixtures.

Autouse isolation (REVIEW.md ASK, "lab/data isolation"): every test in
``tests/nucleus`` gets its own tmp ``ARAIL_DATA_DIR``/``ARAIL_MODELS_DIR``
by default, both as env vars (subprocess CLI tests inherit ``os.environ``)
and as ``arail.config.DATA_DIR``/``MODELS_DIR`` (in-process tests). Without
this, ``nucleus build``'s lock file and ``arail.activity``'s ActivityLog
singleton write into the real checkout's ``lab/data/`` — polluting a real
Buddy/SRE install's activity feed and leaving a stray ``build.lock``.

A test that needs a *specific* data/models dir (e.g. to assert
cross-process sharing) still wins: this fixture runs first, and a test's
own ``monkeypatch.setattr("arail.config.DATA_DIR", ...)`` /
``monkeypatch.setenv("ARAIL_DATA_DIR", ...)`` applied later in the same
test function overrides it, since monkeypatch undoes in LIFO order.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_lab_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "_autouse_lab_data"
    models_dir = tmp_path / "_autouse_lab_models"
    data_dir.mkdir()
    models_dir.mkdir()

    monkeypatch.setenv("ARAIL_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(models_dir))
    monkeypatch.setattr("arail.config.DATA_DIR", data_dir)
    monkeypatch.setattr("arail.config.MODELS_DIR", models_dir)

    # arail.activity.LOG_FILE is a module-level constant computed at import
    # time from the (real) DATA_DIR, and ActivityLog is a process-wide
    # singleton — patching arail.config.DATA_DIR alone does not move it.
    # Repoint the constant and drop any already-constructed singleton so
    # the next ActivityLog() call re-inits against the tmp path.
    import arail.activity as activity_mod

    monkeypatch.setattr(activity_mod, "LOG_FILE", data_dir / "activity.jsonl")
    monkeypatch.setattr(activity_mod.ActivityLog, "_instance", None)

    yield

    # Leave no dangling singleton pointed at a now-deleted tmp_path for the
    # next test in this process.
    activity_mod.ActivityLog._instance = None
