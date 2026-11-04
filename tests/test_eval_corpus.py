"""The committed eval corpus still matches the fixtures it was generated from."""

import json

import pytest
import yaml

from evals import port_fixtures
from evals.corpus import corpus_hash

pytestmark = pytest.mark.unit


def test_every_committed_scenario_matches_its_fixture():
    stale = []
    for path in sorted(port_fixtures.FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario, expect = port_fixtures.convert(path.stem, data)
        scenario_path = port_fixtures.SCENARIOS / f"{path.stem}.yaml"
        gold_path = port_fixtures.GOLD / f"{path.stem}.json"
        if not scenario_path.exists() or not gold_path.exists():
            stale.append(f"{path.stem}: generated file missing")
            continue
        if scenario_path.read_text(encoding="utf-8") != yaml.safe_dump(
            scenario, allow_unicode=True, sort_keys=False
        ):
            stale.append(f"{path.stem}: scenario differs")
        if json.loads(gold_path.read_text(encoding="utf-8")) != expect:
            stale.append(f"{path.stem}: gold differs")
    assert not stale, f"re-run python -m evals.port_fixtures: {stale}"


def test_every_behaviour_scenario_matches_its_fixture():
    stale = []
    for path in sorted(port_fixtures.FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = yaml.safe_dump(
            port_fixtures.behaviour_scenario(path.stem, data),
            allow_unicode=True,
            sort_keys=False,
        )
        target = port_fixtures.BEHAVIOUR / f"{path.stem}.yaml"
        if not target.exists():
            stale.append(f"{path.stem}: behaviour scenario missing")
        elif target.read_text(encoding="utf-8") != expected:
            stale.append(f"{path.stem}: behaviour scenario differs")
    assert not stale, f"re-run python -m evals.port_fixtures: {stale}"


def test_no_scenario_outlives_its_fixture():
    fixtures = {path.stem for path in port_fixtures.FIXTURES.glob("*.json")}
    orphans = [
        path.stem
        for path in sorted(port_fixtures.SCENARIOS.glob("*.yaml"))
        if path.stem not in fixtures
    ]
    assert not orphans, f"scenarios with no fixture: {orphans}"


def corpus_root(tmp_path):
    (tmp_path / "gold").mkdir()
    (tmp_path / "scenarios" / "replay").mkdir(parents=True)
    (tmp_path / "manifest.yaml").write_text("repeats: 1\n")
    (tmp_path / "scenarios" / "replay" / "a.yaml").write_text("name: a\n")
    return tmp_path


def test_corpus_hash_is_stable(tmp_path):
    root = corpus_root(tmp_path)
    assert corpus_hash(root) == corpus_hash(root)


def test_corpus_hash_notices_a_changed_scenario(tmp_path):
    root = corpus_root(tmp_path)
    before = corpus_hash(root)
    (root / "scenarios" / "replay" / "a.yaml").write_text("name: b\n")
    assert corpus_hash(root) != before


def test_corpus_hash_notices_a_changed_recording(tmp_path):
    root = corpus_root(tmp_path)
    before = corpus_hash(root)
    (root / "audio").mkdir()
    (root / "audio" / "a.wav").write_bytes(b"RIFF")
    assert corpus_hash(root) != before


def test_corpus_hash_marks_a_missing_part(tmp_path):
    root = corpus_root(tmp_path)
    without_audio = corpus_hash(root)
    (root / "audio").mkdir()
    assert corpus_hash(root) != without_audio
