import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mdpage
import state_store
import user_study as study


NOW = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)


def book(root):
    file = root / "wiki/domains/demo/concepts/test.md"
    body = "正文 ==高亮==。\n\n" + "\n\n".join(
        f"> [!question] 题目{i}？\n> > [!success]- 答案\n> > 答案{i} $a<b$。" for i in range(8))
    mdpage.write_page(file, {"type": "concept", "status": "published", "domain": "demo",
                           "canonical_id": "concept.demo.test", "source_refs": ["book"]}, body)
    return file


def rate(card, **extra):
    return {"id": card["id"], "revision": card["revision"], "version": card["version"],
            "event_id": str(uuid.uuid4()), "rating": 3, "mode": "planned", **extra}


def test_rating_retry_stale_tab_and_undo_preserve_content(tmp_path):
    file = book(tmp_path)
    original = file.read_bytes()
    study.write(tmp_path, "preferences", {"sources": ["book"]}, now=NOW)
    cards = study.read(tmp_path, "cards", {"mode": "planned"}, now=NOW)["cards"]
    assert len(cards) == 5
    request = rate(cards[0])
    saved = study.write(tmp_path, "review", request, now=NOW)
    assert saved["due"]
    assert study.write(tmp_path, "review", request, now=NOW)["duplicate"]
    with pytest.raises(ValueError, match="different review"):
        study.write(tmp_path, "review", {**request, "mode": "practice", "schedule": False}, now=NOW)
    with pytest.raises(ValueError, match="another tab"):
        study.write(tmp_path, "review", rate(cards[0]), now=NOW)
    assert len(study.read(tmp_path, "history")) == 1
    study.write(tmp_path, "undo", {"event_id": saved["event_id"]}, now=NOW)
    restored = study.read(tmp_path, "cards", now=NOW)["cards"][0]
    assert restored["schedule"] is None
    assert restored["version"] == 2
    assert study.read(tmp_path, "history")[0]["undone"]
    assert file.read_bytes() == original


def test_daily_cap_does_not_change_due_dates_and_practice_is_optional(tmp_path):
    book(tmp_path)
    study.write(tmp_path, "preferences", {"sources": ["book"], "daily_limit": 2, "new_limit": 2}, now=NOW)
    cards = study.read(tmp_path, "cards", {"mode": "planned"}, now=NOW)["cards"]
    for card in cards:
        study.write(tmp_path, "review", rate(card), now=NOW)
    assert study.read(tmp_path, "cards", {"mode": "planned"}, now=NOW)["cards"] == []
    later = NOW + timedelta(days=30)
    before = study.read(tmp_path, "export")["tables"]["study_cards"]
    assert len(study.read(tmp_path, "cards", {"mode": "planned"}, now=later)["cards"]) == 2
    assert study.read(tmp_path, "export")["tables"]["study_cards"] == before
    card = study.read(tmp_path, "cards", now=later)["cards"][0]
    original_due = card["schedule"]["due"]
    study.write(tmp_path, "review", rate(card, mode="practice", schedule=False), now=later)
    card = study.read(tmp_path, "cards", now=later)["cards"][0]
    assert card["schedule"]["due"] == original_due


@pytest.mark.parametrize("timezone_offset", [480, -300])
def test_daily_cap_uses_local_midnight_and_excludes_future_and_undone(tmp_path, timezone_offset):
    book(tmp_path)
    study.write(tmp_path, "preferences", {"sources": ["book"], "daily_limit": 1,
                                         "timezone_offset": timezone_offset}, now=NOW)
    cards = study.read(tmp_path, "cards", {"mode": "practice"}, now=NOW)["cards"]
    offset = timedelta(minutes=timezone_offset)
    start = (NOW + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
    for card, at in [(cards[0], start - timedelta(seconds=1)), (cards[1], start + timedelta(days=1))]:
        study.write(tmp_path, "review", rate(card, mode="practice", schedule=True), now=at)
    saved = study.write(tmp_path, "review", rate(cards[2]), now=start)
    with pytest.raises(ValueError, match="plan is complete"):
        study.write(tmp_path, "review", rate(cards[3]), now=NOW)
    study.write(tmp_path, "undo", {"event_id": saved["event_id"]}, now=NOW)
    study.write(tmp_path, "review", rate(cards[3]), now=NOW)


def test_backup_roundtrip_and_duplicate_import(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    book(left)
    book(right)
    card = study.read(left, "cards", now=NOW)["cards"][0]
    study.write(left, "review", rate(card, mode="practice", schedule=True), now=NOW)
    backup = study.read(left, "export", now=NOW)
    preview = study.write(right, "import", {"backup": backup}, now=NOW)
    assert preview["additions"] == 2
    study.write(right, "import", {"backup": backup, "apply": True, "policy": "keep"}, now=NOW)
    assert study.read(right, "history") == study.read(left, "history")
    again = study.write(right, "import", {"backup": backup, "apply": True, "policy": "keep"}, now=NOW)
    assert again["additions"] == 0 and again["conflicts"] == []
    assert len(study.read(right, "history")) == 1


def test_changed_answer_keeps_identity_and_requires_current_revision(tmp_path):
    file = book(tmp_path)
    first = study.read(tmp_path, "cards", now=NOW)["cards"][0]
    study.write(tmp_path, "review", rate(first, mode="practice", schedule=True), now=NOW)
    file.write_text(file.read_text(encoding="utf-8").replace("答案0 $", "修订答案0 $"), encoding="utf-8")
    current = study.read(tmp_path, "cards", now=NOW)["cards"][0]
    assert current["id"] == first["id"] and current["revision"] != first["revision"] and current["changed"]
    with pytest.raises(ValueError, match="answer changed"):
        study.write(tmp_path, "review", rate(first), now=NOW)


def test_annotation_and_question_only_write_personal_records(tmp_path):
    file = book(tmp_path)
    original = file.read_bytes()
    payload = {"page": file.relative_to(tmp_path / "wiki").as_posix(), "quote": "正文", "text": "为什么？",
               "revision": hashlib.sha256(original).hexdigest(), "source": "book"}
    note = study.write(tmp_path, "flag", payload, now=NOW)
    import reader_data
    assert reader_data.read(tmp_path, "proposals")[0]["question"]["quote"] == "正文"
    assert note["proposal_id"] > 0
    assert study.read(tmp_path, "annotations")[0]["quote"] == "正文"
    assert file.read_bytes() == original
    with pytest.raises(ValueError, match="Verify"):
        study.write(tmp_path, "proposal-decision", {"id": note["proposal_id"], "expected_status": "open",
                    "decision": "resolved", "reason": "已对照"}, now=NOW)
    study.write(tmp_path, "proposal-decision", {"id": note["proposal_id"], "expected_status": "open",
                "decision": "resolved", "reason": "已对照原文，问题不存在", "verified": True}, now=NOW)


def test_reading_empty_state_does_not_create_database(tmp_path):
    assert study.read(tmp_path, "state")["settings"]["daily_limit"] == 20
    assert not (tmp_path / "pipeline-workspace").exists()


def test_short_interval_relearning_returns_without_consuming_another_daily_slot(tmp_path):
    book(tmp_path)
    study.write(tmp_path, "preferences", {"sources": ["book"], "daily_limit": 1, "new_limit": 1}, now=NOW)
    card = study.read(tmp_path, "cards", {"mode": "planned"}, now=NOW)["cards"][0]
    study.write(tmp_path, "review", rate(card, rating=1), now=NOW)
    later = NOW + timedelta(minutes=2)
    cards = study.read(tmp_path, "cards", {"mode": "planned"}, now=later)["cards"]
    assert [c["id"] for c in cards] == [card["id"]]
    summary = study.read(tmp_path, "summary", now=later)
    assert summary["completed"] == summary["planned"] == summary["followups"] == 1
    study.write(tmp_path, "review", rate(cards[0]), now=later)
    summary = study.read(tmp_path, "summary", now=later)
    assert summary["completed"] == 1 and summary["repetitions"] == 1

def test_practice_does_not_certify_changed_answer(tmp_path):
    file = book(tmp_path)
    first = study.read(tmp_path, 'cards', now=NOW)['cards'][0]
    study.write(tmp_path, 'review', rate(first, mode='practice', schedule=True), now=NOW)
    file.write_text(file.read_text(encoding='utf-8').replace('答案0 $', '修订答案0 $'), encoding='utf-8')
    changed = study.read(tmp_path, 'cards', now=NOW)['cards'][0]
    study.write(tmp_path, 'review', rate(changed, mode='practice', schedule=False), now=NOW)
    assert study.read(tmp_path, 'cards', now=NOW)['cards'][0]['changed']


def test_backup_conflict_replaces_card_history_explicitly_and_invalidates_stale_tab(tmp_path):
    book(tmp_path)
    first = study.read(tmp_path, 'cards', now=NOW)['cards'][0]
    study.write(tmp_path, 'review', rate(first, mode='practice', schedule=True), now=NOW)
    backup = study.read(tmp_path, 'export', now=NOW)
    second = study.read(tmp_path, 'cards', now=NOW)['cards'][0]
    study.write(tmp_path, 'review', rate(second, mode='practice', schedule=True), now=NOW + timedelta(days=2))
    stale = study.read(tmp_path, 'cards', now=NOW)['cards'][0]
    preview = study.write(tmp_path, 'import', {'backup': backup}, now=NOW)
    assert preview['replaced_history'] == 1
    with pytest.raises(ValueError, match='Confirm'):
        study.write(tmp_path, 'import', {'backup': backup, 'apply': True, 'policy': 'replace'}, now=NOW)
    assert len(study.read(tmp_path, 'history')) == 2
    request = {'backup': backup, 'apply': True, 'policy': 'replace', 'confirm': 'replace'}
    study.write(tmp_path, 'import', request, now=NOW)
    assert len(study.read(tmp_path, 'history')) == 1
    assert study.read(tmp_path, 'cards', now=NOW)['cards'][0]['version'] > stale['version']
    assert study.write(tmp_path, 'import', request, now=NOW)['conflicts'] == []


def test_event_id_cannot_be_reused_for_another_question(tmp_path):
    book(tmp_path)
    cards = study.read(tmp_path, 'cards', now=NOW)['cards']
    request = rate(cards[0], mode='practice')
    study.write(tmp_path, 'review', request, now=NOW)
    with pytest.raises(ValueError, match='different review'):
        study.write(tmp_path, 'review', rate(cards[1], mode='practice', event_id=request['event_id']), now=NOW)
