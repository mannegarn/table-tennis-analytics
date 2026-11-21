"""Unit tests for parsing and validating match_details backup JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tt_stats.matches.backup_seed import load_backup_file


def test_load_backup_file_parses_matches_and_hashes(tmp_path: Path) -> None:
    path = tmp_path / "2234_match_details.json"
    path.write_text(
        json.dumps(
            [
                {
                    "eventId": "2234",
                    "documentCode": "TTEMSINGLES-----------FNL-000100----------",
                    "resultStatus": "OFFICIAL",
                },
                {
                    "eventId": 2234,
                    "documentCode": "  TTEWSINGLES-----------FNL-000100----------  ",
                    "resultStatus": "OFFICIAL",
                },
            ]
        ),
        encoding="utf-8",
    )

    matches = load_backup_file(path)

    assert len(matches) == 2
    assert matches[0].event_id == 2234
    assert matches[0].document_code == "TTEMSINGLES-----------FNL-000100----------"
    assert matches[0].payload["resultStatus"] == "OFFICIAL"
    assert len(matches[0].payload_sha256) == 64

    assert matches[1].event_id == 2234
    assert matches[1].document_code == "TTEWSINGLES-----------FNL-000100----------"
    assert matches[0].payload_sha256 != matches[1].payload_sha256


def test_load_backup_file_rejects_non_list_root(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"eventId": 2234}), encoding="utf-8")

    with pytest.raises(ValueError, match="expected a JSON list"):
        load_backup_file(path)


def test_load_backup_file_rejects_non_dict_entry(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not-a-dict"]), encoding="utf-8")

    with pytest.raises(ValueError, match="expected match dict"):
        load_backup_file(path)


@pytest.mark.parametrize("bad_event_id", [None, "", "abc", 0, -5, True])
def test_load_backup_file_rejects_invalid_event_id(
    tmp_path: Path,
    bad_event_id: Any,
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps([{"eventId": bad_event_id, "documentCode": "DOC1"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="eventId"):
        load_backup_file(path)


@pytest.mark.parametrize("bad_doc_code", [None, "", "   ", 123])
def test_load_backup_file_rejects_invalid_document_code(
    tmp_path: Path,
    bad_doc_code: Any,
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps([{"eventId": 2234, "documentCode": bad_doc_code}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="documentCode"):
        load_backup_file(path)
