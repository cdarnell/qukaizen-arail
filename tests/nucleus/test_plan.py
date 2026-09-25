"""arail.nucleus.plan — template write + preflight-only load."""

from __future__ import annotations

import pytest

from arail.nucleus import plan
from arail.nucleus.errors import DomainConfigError


def test_write_domain_template(tmp_path):
    d = tmp_path / "domains"
    path = plan.write_domain_template("track kernel CVEs", "my-domain", domains_dir=d)
    assert path == d / "my-domain.yaml"
    text = path.read_text()
    assert "shard: qkz-my-domain" in text
    assert (d / "my-domain.eyeball.txt").is_file()
    assert len([ln for ln in (d / "my-domain.eyeball.txt").read_text().splitlines() if ln.strip()]) == 10


def test_write_domain_template_bad_slug(tmp_path):
    with pytest.raises(DomainConfigError):
        plan.write_domain_template("x", "Not Valid", domains_dir=tmp_path)


def test_write_domain_template_refuses_overwrite(tmp_path):
    d = tmp_path / "domains"
    plan.write_domain_template("x", "dup", domains_dir=d)
    with pytest.raises(DomainConfigError, match="already exists"):
        plan.write_domain_template("x", "dup", domains_dir=d)


def test_run_create_mode(tmp_path, monkeypatch, capsys):
    from arail.nucleus import domain as domain_mod

    monkeypatch.setattr(domain_mod, "_DOMAINS_DIR", tmp_path / "domains")
    monkeypatch.setattr(plan, "_DOMAINS_DIR", tmp_path / "domains")
    code = plan.run(["track kernel CVEs", "--name", "my-domain"])
    assert code == 0
    out = capsys.readouterr().out
    assert "wrote" in out


def test_run_preflight_mode_loads_existing_domain(tmp_path, monkeypatch, capsys):
    d = tmp_path / "domains"
    path = plan.write_domain_template("x", "existing", domains_dir=d)
    # Fill in the template's REPLACE_ME source so it loads cleanly.
    text = path.read_text().replace("git:REPLACE_ME", "git:linux")
    path.write_text(text)

    # run() resolves domains via arail.nucleus.domain's module-level
    # _DOMAINS_DIR constant, so point it at our tmp dir for this call, and
    # validates student.base via arail.nucleus.models.resolve_model, so a
    # fake model dir needs to exist under ARAIL_MODELS_DIR.
    from arail.nucleus import domain as domain_mod

    monkeypatch.setattr(domain_mod, "_DOMAINS_DIR", d)
    models_dir = tmp_path / "models"
    student_dir = models_dir / "Qwen2.5-3B-Instruct-4bit"
    student_dir.mkdir(parents=True)
    (student_dir / "config.json").write_text('{"num_parameters": 3000000000}')
    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))

    code = plan.run(["existing"])
    assert code == 0
    assert "loads cleanly" in capsys.readouterr().out


def test_run_bad_args():
    with pytest.raises(DomainConfigError):
        plan.run([])
