"""Full-context coverage, immutable source loading, and executable migration controls."""
import io
import json
import subprocess
import tarfile

import pytest

from blindspot.providers import Answer, DecisionResult
from blindspot.repository import (
    DEFAULT_TASK, RepositoryError, Snapshot, context, curate, load_demo,
    load_github, load_local, make_snapshot, propose_change,
)
from blindspot.context_benchmark import (
    REQUIRED_EDITS, benchmark, execute_acceptance, validate_edits,
)


def test_fixture_contains_real_source_and_no_evaluation_labels():
    snapshot = load_demo()
    assert len(snapshot.files) == 56
    state = context(snapshot, DEFAULT_TASK)
    assert "[FILE atlas/auth.py]" in state and "1: def can_read" in state
    assert "required_files" not in state and "REQUIRED_EDITS" not in state
    assert "Frozen acceptance" not in state


def test_sensitive_and_binary_files_are_excluded_before_model_context():
    snapshot = make_snapshot("test", "sha", [
        ("app.py", b"value = 1"), (".env", b"PASSWORD=never-send"),
        ("secrets.json", b'{"secret":"never-send"}'),
        ("image.py", b"\x00binary"), ("private.py", b"-----BEGIN PRIVATE KEY-----"),
    ])
    assert list(snapshot.files) == ["app.py"]
    assert "never-send" not in context(snapshot, "Change app behavior")
    assert len(snapshot.excluded) == 4


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, content in entries:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    return stream.getvalue()


def test_github_snapshot_is_sha_pinned_and_never_extracted(monkeypatch):
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://github-api.invalid")
    calls = []
    sha = "a" * 40
    def download(url, limit):
        calls.append(url)
        if "github-api.invalid" in url:
            return json.dumps({"sha": sha}).encode()
        return archive([("repo-sha/app.py", b"value=1"), ("repo-sha/.env", b"secret")])
    snapshot = load_github("https://github.com/owner/repo", "main", downloader=download)
    assert snapshot.revision == sha and list(snapshot.files) == ["app.py"]
    assert calls[1] == f"https://codeload.github.com/owner/repo/tar.gz/{sha}"


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://other.example/a/b", "https://github.com/a/b/tree/main", "https://github.com/a/b?token=x", "https://github.com@other.example/a/b"])
def test_github_loader_rejects_other_destinations(url):
    with pytest.raises(RepositoryError):
        load_github(url, downloader=lambda *a: pytest.fail("Invalid URL reached network"))


def test_archive_traversal_is_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://github-api.invalid")
    def download(url, limit):
        return json.dumps({"sha": "a" * 40}).encode() if "github-api.invalid" in url else archive([("root/../outside.py", b"x=1")])
    with pytest.raises(RepositoryError, match="Unsafe"):
        load_github("https://github.com/owner/repo", downloader=download)


class SnapStub:
    available = True
    def __init__(self, value=0.8):
        self.calls = []
        self.value = value

    def decide(self, state, questions, **kwargs):
        self.calls.append((state, questions))
        return DecisionResult(answers={key: Answer(key, "noul", self.value) for key in questions}, input_tokens=1000)


def test_each_batch_contains_the_entire_snapshot_not_just_its_file_list():
    files = {f"m{i}.py": f"unique_marker_{i} = {i}" for i in range(193)}
    snapshot = Snapshot("test", "sha", files, [])
    snap = SnapStub()
    report = curate(snapshot, "Change the module interfaces", snap=snap)
    assert len(snap.calls) == 3 and report["actual_input_tokens"] == 3000
    assert len(report["selected"]) == 193
    assert all(len(q) <= 96 for _, q in snap.calls)
    for state, _ in snap.calls:
        assert all(source in state for source in files.values())
    assert len({state for state, _ in snap.calls}) == 1


@pytest.mark.parametrize("value", [float("nan"), -0.1, 1.1, "0.8"])
def test_invalid_model_scores_do_not_turn_into_file_selections(value):
    with pytest.raises(RepositoryError, match="invalid file probability"):
        curate(load_demo(), DEFAULT_TASK, snap=SnapStub(value))


def test_missing_model_key_does_not_silently_use_keyword_search():
    snap = SnapStub()
    snap.available = False
    with pytest.raises(RepositoryError, match="not configured"):
        curate(load_demo(), DEFAULT_TASK, snap=snap)


def test_oversized_context_is_refused_before_any_model_call():
    snap = SnapStub()
    snapshot = Snapshot("test", "sha", {"large.py": "x=" + "1" * 1_200_000}, [])
    with pytest.raises(RepositoryError, match="budget"):
        curate(snapshot, DEFAULT_TASK, snap=snap)
    assert not snap.calls


def reference_selection(snapshot):
    return {"source_fingerprint": snapshot.fingerprint, "task": DEFAULT_TASK,
            "method": "labelled reference", "selected": list(REQUIRED_EDITS)}


def test_reference_migration_changes_ten_files_and_passes_independent_contract():
    snapshot = load_demo()
    before = snapshot.fingerprint
    report = benchmark(snapshot, reference_selection(snapshot))
    first = report["arms"][0]
    assert first["verdict"] == "PASS" and first["passed"] == first["total"] == 14
    assert len(first["changed_files"]) == 10
    assert all(a["contract_hash"] == report["contract_hash"] for a in report["arms"])
    assert snapshot.fingerprint == before
    assert "--- a/atlas/" in first["diff"]


def test_partial_context_produces_a_real_incomplete_migration():
    snapshot = load_demo()
    selection = reference_selection(snapshot)
    selection["selected"] = ["atlas/models.py", "atlas/schemas.py"]
    result = benchmark(snapshot, selection)["arms"][0]
    assert result["verdict"] == "BLOCK" and result["passed"] < result["total"]
    assert "atlas/api.py" in result["missed_files"]


def test_naive_legacy_replacement_fails_compatibility_check():
    snapshot = load_demo()
    candidate = validate_edits(snapshot, ["atlas/legacy.py"], [{"file": "atlas/legacy.py", "old": "tenant_id", "new": "workspace_id"}])
    checks = execute_acceptance(snapshot.files, candidate)
    legacy = next(c for c in checks if c["name"] == "legacy input remains compatible")
    assert not legacy["passed"]


@pytest.mark.parametrize("edit", [
    {"file":"../../outside.py", "old":"tenant_id", "new":"workspace_id"},
    {"file":"atlas/api.py", "old":"tenant_id", "new":"workspace_id; import os"},
    {"file":"atlas/api.py", "old":"nonexistent", "new":"workspace_id"},
])
def test_patch_cannot_inject_code_or_edit_outside_selected_context(edit):
    with pytest.raises(RepositoryError):
        validate_edits(load_demo(), ["atlas/api.py"], [edit])


def test_arbitrary_downloaded_repository_never_reaches_test_execution():
    snapshot = load_demo()
    snapshot.demo = False
    with pytest.raises(RepositoryError, match="inspection-only"):
        benchmark(snapshot, reference_selection(snapshot))


def test_different_task_cannot_be_scored_against_unrelated_acceptance_tests():
    snapshot = load_demo()
    selection = reference_selection(snapshot)
    selection["task"] = "An unrelated feature"
    with pytest.raises(RepositoryError, match="does not match"):
        benchmark(snapshot, selection)


def test_http_workflow_uses_server_stored_selection_not_client_claims():
    from serve import perform_action
    loaded = perform_action("/api/repository/load", {"source": "demo"})
    selected = perform_action("/api/repository/curate", {"id": loaded["id"], "task": DEFAULT_TASK, "method": "keyword"})
    result = perform_action("/api/repository/benchmark", {"id": loaded["id"], "selection_id": selected["selection_id"], "generator": "reference"})
    assert result["arms"][-1]["verdict"] == "PASS"
    with pytest.raises(RepositoryError, match="before"):
        perform_action("/api/repository/benchmark", {"id": loaded["id"], "selection_id": "forged", "generator": "reference"})


class MuseStub:
    available = True
    model = "stub-not-a-live-result"

    def __init__(self, edits):
        self.edits = edits
        self.calls = []

    def complete(self, system, user, **kwargs):
        self.calls.append((system, user))
        return json.dumps(self.edits)


def test_real_repo_draft_is_selected_only_unverified_and_applyable(tmp_path):
    original = {"app.py": "value = 1", "other.py": "not_sent_to_patcher = True\n"}
    snapshot = Snapshot("real-repo", "sha", original, [])
    selection = {"source_fingerprint": snapshot.fingerprint, "selected": ["app.py"], "task": "Set value to two"}
    muse = MuseStub([{"file": "app.py", "old": "value = 1", "new": "value = 2"}])
    report = propose_change(snapshot, selection, muse=muse)
    assert report["verdict"] == "UNVERIFIED" and report["changed_files"] == ["app.py"]
    assert snapshot.files == original
    assert "not_sent_to_patcher" not in muse.calls[0][1]
    assert "\\ No newline at end of file" in report["diff"]
    (tmp_path / "app.py").write_text(original["app.py"])
    result = subprocess.run(["git", "apply", "--check", "-"], input=report["diff"], text=True, capture_output=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("edits,match", [
    ([{"file": "other.py", "old": "x", "new": "y"}], "outside"),
    ([{"file": "app.py", "old": "missing", "new": "y"}], "missing or ambiguous"),
    ([{"file": "app.py", "old": "value", "new": "value"}], "no source changes"),
    ([{"file": "app.py", "old": "value", "new": "x", "command": "ignored"}], "shape"),
])
def test_real_repo_patch_rejects_unusable_or_unselected_edits(edits, match):
    snapshot = Snapshot("real-repo", "sha", {"app.py": "value = 1\n", "other.py": "x\n"}, [])
    selection = {"source_fingerprint": snapshot.fingerprint, "selected": ["app.py"], "task": "Change value"}
    with pytest.raises(RepositoryError, match=match):
        propose_change(snapshot, selection, muse=MuseStub(edits))


def test_meta_missing_key_does_not_return_a_reference_patch():
    snapshot = load_demo()
    muse = MuseStub([])
    muse.available = False
    with pytest.raises(RepositoryError, match="not configured"):
        propose_change(snapshot, reference_selection(snapshot), muse=muse)
    assert not muse.calls


def test_local_snapshot_reads_commit_not_modified_working_copy(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init")
    (tmp_path / "app.py").write_text("committed = True\n")
    (tmp_path / ".env").write_text("SECRET=never_send\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    (tmp_path / "app.py").write_text("working_copy = True\n")
    snapshot = load_local(tmp_path)
    assert snapshot.files == {"app.py": "committed = True\n"}
    assert any(e["path"] == ".env" for e in snapshot.excluded)
