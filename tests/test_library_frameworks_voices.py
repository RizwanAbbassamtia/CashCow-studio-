"""The frameworks and voices libraries: when a framework's text counts as extracted, that a
framework path outside the shared folder is never looked at, and that the voice consent
verdict is the voice step's own."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cashcow_studio.library.frameworks import framework_entry, is_extracted, list_frameworks
from cashcow_studio.library.voices import MISSING_CONSENT, list_voices
from cashcow_studio.llm.frameworks import cache_path, extract_text, framework_cache_dir
from cashcow_studio.models.channel import Channel, Framework
from cashcow_studio.pipeline.stages.voice import consent_status
from test_library_api import write_file, write_wav


def make_channel(slug: str = "kind-ledger", **sections: Any) -> Channel:
    return Channel.model_validate(
        {"slug": slug, "channel": {"name": slug.replace("-", " ").title()}, **sections}
    )


# Frameworks ------------------------------------------------------------------------------------


def test_extracted_means_the_text_is_ready(tmp_path: Path) -> None:
    folder = tmp_path / "frameworks"
    cache_dir = framework_cache_dir(tmp_path / "app")
    assert cache_dir is not None
    plain = write_file(folder / "rules.md", "# Rules")
    assert is_extracted(plain, cache_dir) is True  # text needs no extraction

    named = write_file(folder / "guide.pdf", b"%PDF")
    assert is_extracted(named, cache_dir) is False
    write_file(folder / "guide.extracted.txt", "Guide text")  # <stem>.extracted.txt
    assert is_extracted(named, cache_dir) is True

    full = write_file(folder / "script.pdf", b"%PDF")
    write_file(folder / "script.pdf.extracted.txt", "Script text")  # <name>.extracted.txt
    assert is_extracted(full, cache_dir) is True

    # The app's own copy (what the stages write under the app folder) counts while it is
    # at least as new as the file.
    read = write_file(folder / "titles.txt.pdf", b"%PDF")
    copy = write_file(cache_path(read, cache_dir), "Title text")
    assert is_extracted(read, cache_dir) is True
    os.utime(copy, (read.stat().st_mtime - 60,) * 2)
    assert is_extracted(read, cache_dir) is False
    assert is_extracted(read, None) is False


def test_the_stages_cache_marks_a_framework_extracted(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    source = write_file(shared / "channels" / "kind-ledger" / "frameworks" / "title.txt", "Rules")
    cache_dir = framework_cache_dir(tmp_path / "app")
    extract_text(source, cache_dir)  # what a stage does the first time it reads the file
    assert cache_path(source, cache_dir).is_file()
    assert is_extracted(source, cache_dir) is True


def test_paths_outside_the_shared_folder_are_never_looked_at(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    secret = write_file(tmp_path / "app" / ".env", "ANTHROPIC_API_KEY=sk-ant-secret\n")
    outside = write_file(tmp_path / "elsewhere" / "notes.txt", "outside the team folder")
    folder = write_file(shared / "channels" / "kind-ledger" / "frameworks" / "dir.txt" / "x", "")
    channel = make_channel(frameworks=[
        {"type": "title", "name": "Full path", "path": str(outside)},
        {"type": "title", "name": "Climbing", "path": "../elsewhere/notes.txt"},
        {"type": "title", "name": "Hidden", "path": "../app/.env"},
        {"type": "title", "name": "Program", "path": "channels/kind-ledger/run.exe"},
        {"type": "title", "name": "Empty", "path": ""},
        {"type": "title", "name": "A folder", "path": "channels/kind-ledger/frameworks/dir.txt"},
    ])
    rows = {row.name: row for row in list_frameworks([channel], shared, tmp_path / "app")}
    for name in ("Full path", "Climbing", "Hidden", "Program"):
        row = rows[name]
        assert row.exists is False and row.size_bytes is None and row.updated_at is None, name
        assert row.extracted is False and row.problem, name
    assert "full path" in rows["Full path"].problem
    assert "leaves the shared folder" in rows["Climbing"].problem
    assert "not a text or PDF file" in rows["Program"].problem
    assert rows["Empty"].problem == "No file has been chosen for this framework yet."
    assert rows["A folder"].problem.endswith("is a folder, not a file.")
    assert folder.parent.is_dir() and secret.is_file()


def test_framework_entry_copies_the_channel_fields(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    write_file(shared / "channels" / "kind-ledger" / "frameworks" / "seo.md", "SEO rules")
    channel = make_channel()
    framework = Framework(type="seo", name="SEO", path="channels/kind-ledger/frameworks/seo.md",
                          version="3", formats="shorts", notes="ignored")
    row = framework_entry(channel, framework, shared, None)
    assert row.model_dump(exclude={"updated_at"}) == {
        "channel_slug": "kind-ledger", "channel_name": "Kind Ledger", "type": "seo",
        "name": "SEO", "path": "channels/kind-ledger/frameworks/seo.md", "version": "3",
        "formats": "shorts", "size_bytes": 9, "extracted": True, "exists": True,
        "is_link": False, "problem": None,
    }
    assert row.updated_at is not None and row.updated_at.tzinfo is not None


# Voices ----------------------------------------------------------------------------------------


CONSENT = {"owner_name": "Daniel Reed", "consented_by": "Imran",
           "consented_at": "2026-09-01T10:00:00Z"}


def test_consent_json_next_to_the_sample(tmp_path: Path) -> None:
    sample = write_wav(tmp_path / "voices" / "daniel.wav", 0.2)
    record = write_file(tmp_path / "voices" / "consent.json", json.dumps(CONSENT))
    channel = make_channel(voice={"tool": "ai33", "sample_path": str(sample)})
    (row,) = list_voices([channel], tmp_path / "shared")
    assert row.sample_exists is True and row.clone_ref == ""
    assert row.consent.present is True and row.consent.required is True  # a sample = a clone
    assert row.consent.owner_name == "Daniel Reed" and row.consent.ref == str(record)
    assert row.consent.consented_at is not None and row.consent.consented_at.year == 2026
    assert row.consent.problem is None


def test_consent_problems_in_plain_english(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    clone_without = make_channel("no-record", voice={"tool": "ai33", "clone_ref": "c-1"})
    half_filled = make_channel("half", voice={
        "tool": "ai33", "clone_ref": "c-2", "consent": {"owner_name": "Daniel Reed"},
    })
    stock_with_record = make_channel("stock", voice={"tool": "minimax", "consent": CONSENT})
    rows = {row.channel_slug: row for row in list_voices(
        [clone_without, half_filled, stock_with_record], shared
    )}
    assert rows["no-record"].consent.present is False
    assert rows["no-record"].consent.required is True
    assert rows["no-record"].consent.problem == MISSING_CONSENT
    half = rows["half"].consent
    assert half.present is False and half.required is True
    assert half.problem == (
        "The Consent fields on the channel's Voice tab are missing who recorded the consent "
        "and the date of the consent."
    )
    stock = rows["stock"].consent
    assert stock.required is False and stock.present is True and stock.problem is None


def test_verdict_matches_the_voice_step(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    channels = [
        make_channel("clone", voice={"tool": "ai33", "clone_ref": "c-1", "consent": CONSENT}),
        make_channel("clone-bare", voice={"tool": "ai33", "clone_ref": "c-2"}),
        make_channel("stock", voice={"tool": "cartesia"}),
    ]
    for row, channel in zip(list_voices(channels, shared), channels, strict=True):
        step = consent_status(channel, shared / "channels")
        assert row.consent.required == step.is_clone, channel.slug
        if step.is_clone:
            assert row.consent.present == step.found, channel.slug


def test_sample_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "shared"
    write_wav(shared / "voices" / "team.wav", 0.1)
    write_wav(tmp_path / "mine" / "own.wav", 0.1)
    monkeypatch.setenv("CCS_TEST_VOICES", str(tmp_path / "mine"))
    channels = [
        make_channel("relative", voice={"sample_path": "voices/team.wav"}),
        make_channel("variable", voice={"sample_path": os.path.join("%CCS_TEST_VOICES%",
                                                                    "own.wav")}),
        make_channel("missing", voice={"sample_path": str(tmp_path / "gone.wav")}),
        make_channel("none", voice={}),
    ]
    exists = {row.channel_slug: row.sample_exists for row in list_voices(channels, shared)}
    assert exists == {"relative": True, "variable": True, "missing": False, "none": False}
