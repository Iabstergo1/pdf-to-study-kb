"""Personal learning data in the existing business SQLite, never in wiki pages.

FSRS receives explicit UTC timestamps and disables fuzzing. Every rating stores
the prior and resulting state in the same transaction; retries use event IDs.
JSON backups contain personal records only, not source material or credentials.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import site_data
import site_exporter
import site_render
import state_store


DEFAULTS = {"sources": [], "daily_limit": 20, "new_limit": 5, "timezone_offset": 0}
TABLES = ("study_cards", "study_reviews", "study_notes", "study_state")
KEYS = {"study_cards": "id", "study_reviews": "event_id", "study_notes": "id", "study_state": "key"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Review time must include a timezone")
    return value.astimezone(timezone.utc)


def _db(root):
    return Path(root) / "pipeline-workspace/state/study-kb.sqlite"


def _snapshot(root):
    db = _db(root)
    result = {table: [] for table in TABLES}
    if not db.exists():
        return result
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table in existing:
                result[table] = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
    return result


def _settings(snapshot):
    row = next((r for r in snapshot["study_state"] if r["key"] == "preferences"), None)
    return {**DEFAULTS, **(json.loads(row["value_json"]) if row else {})}


def _catalogue(root):
    pages = site_exporter._vault_pages(Path(root) / "wiki")
    return site_data.build_quiz_items(pages, site_render.render_page_body, vault=Path(root) / "wiki")


def _day(at, offset):
    return (datetime.fromisoformat(at) + timedelta(minutes=offset)).date()


def _today(snapshot, settings, now):
    date = (now + timedelta(minutes=settings["timezone_offset"])).date()
    return [r for r in snapshot["study_reviews"] if not r["undone"]
            and _day(r["reviewed_at"], settings["timezone_offset"]) == date]


def _eligible(card, settings):
    return bool(set(card.get("source_refs", [])) & set(settings["sources"]))


def _plan(cards, snapshot, settings, now):
    today = _today(snapshot, settings, now)
    scheduled = [r for r in today if r["scheduled"]]
    completed = {r["card_id"] for r in scheduled}
    new_done = {r["card_id"] for r in scheduled if not r["previous_json"]
                or not json.loads(r["previous_json"]).get("schedule_json")}
    due, new = [], []
    for card in cards:
        if card.get("unanswered") or not _eligible(card, settings):
            continue
        if not card.get("schedule"):
            new.append(card)
        elif card["changed"] or datetime.fromisoformat(card["schedule"]["due"]) <= now:
            due.append(card)
    from fsrs import Card, Scheduler
    scheduler = Scheduler(enable_fuzzing=False)
    def priority(card):
        recall = scheduler.get_card_retrievability(Card.from_json(_json(card["schedule"])), now)
        return (not card["changed"], recall, card["schedule"]["due"], card["id"])
    due.sort(key=priority)
    remaining = max(0, settings["daily_limit"] - len(completed))
    followups = [card for card in due if card["id"] in completed]
    chosen = followups + [card for card in due if card["id"] not in completed][:remaining]
    distinct = len(chosen) - len(followups)
    chosen += new[:min(max(0, remaining - distinct), max(0, settings["new_limit"] - len(new_done)))]
    return chosen, {"completed": len(completed), "planned": len(completed) + len(chosen) - len(followups),
                    "remaining": len(chosen), "due": len(due), "new_available": len(new),
                    "followups": len(followups),
                    "repetitions": max(0, len(today) - len({r["card_id"] for r in today})),
                    "practised": len([r for r in today if r["mode"] == "practice"])}


def read(root, action, payload=None, *, now=None):
    payload, now = payload or {}, _now(now)
    snapshot = _snapshot(root)
    if action == "export":
        snapshot["study_state"] = [r for r in snapshot["study_state"] if not r["key"].startswith("proposal:")]
        return {"format": "study-kb-personal", "version": 1, "exported_at": now.isoformat(), "tables": snapshot}
    if action == "annotations":
        return [{**json.loads(r["data_json"]), "version": r["version"]} for r in snapshot["study_notes"]
                if not r["deleted"] and (not payload.get("page") or r["page"] == payload["page"])]
    if action == "history":
        return [{"id": r["event_id"], "card_id": r["card_id"], "at": r["reviewed_at"],
                 "rating": r["rating"], "mode": r["mode"], "scheduled": bool(r["scheduled"]),
                 "undone": bool(r["undone"]),
                 "card": json.loads(json.loads(r["after_json"])["data_json"])}
                for r in sorted(snapshot["study_reviews"], key=lambda r: r["seq"], reverse=True)]
    settings = _settings(snapshot)
    states = {r["key"]: json.loads(r["value_json"]) for r in snapshot["study_state"]}
    if action == "state":
        return {"settings": settings, "state": states}
    by_id = {r["id"]: r for r in snapshot["study_cards"]}
    cards = []
    for item in _catalogue(root):
        stored = by_id.get(item["id"], {})
        cards.append({**item, "version": stored.get("version", 0),
                      "schedule": json.loads(stored["schedule_json"]) if stored.get("schedule_json") else None,
                      "changed": bool(stored and stored["revision"] != item["revision"])})
    planned, stats = _plan(cards, snapshot, settings, now)
    if action == "summary":
        recent = sorted((value for key, value in states.items() if key.startswith("reading:")),
                        key=lambda r: r.get("at", ""), reverse=True)[:5]
        return {"settings": settings, **stats, "total": len(cards), "recent": recent,
                "sources": sorted({s for c in cards for s in c["source_refs"]}),
                "domains": sorted({c["domain"] for c in cards}),
                "unanswered": sum(bool(c.get("unanswered")) for c in cards)}
    if action == "cards":
        selected = planned if payload.get("mode") == "planned" else cards
        selected = [c for c in selected if (not payload.get("source") or payload["source"] in c["source_refs"])
                    and (not payload.get("domain") or c["domain"] == payload["domain"])
                    and (not payload.get("page") or c["rel"] == payload["page"])]
        return {"cards": selected, "stats": stats}
    raise ValueError("Unknown study read action")


def _preferences(value):
    result = {**DEFAULTS, **value}
    for key, minimum, maximum in (("daily_limit", 1, 500), ("new_limit", 0, 100), ("timezone_offset", -840, 840)):
        if type(result[key]) is not int or not minimum <= result[key] <= maximum:
            raise ValueError(f"Invalid {key}")
    if not isinstance(result["sources"], list) or not all(isinstance(s, str) for s in result["sources"]):
        raise ValueError("Choose source IDs")
    return result


def _put_card(con, row):
    con.execute("INSERT OR REPLACE INTO study_cards VALUES (?,?,?,?,?,?,?)",
                tuple(row[k] for k in ("id", "revision", "page", "data_json", "schedule_json", "version", "updated_at")))


def _review(con, card, value, now):
    event_id = str(uuid.UUID(value["event_id"]))
    existing = con.execute("SELECT * FROM study_reviews WHERE event_id=?", (event_id,)).fetchone()
    if existing:
        if (existing["card_id"] != value["id"] or existing["rating"] != value.get("rating")
                or existing["revision"] != value.get("revision") or existing["mode"] != value.get("mode")
                or bool(existing["scheduled"]) != (value.get("mode") == "planned" or value.get("schedule") is True)):
            raise ValueError("This event ID belongs to a different review")
        return {"event_id": event_id, "card": json.loads(existing["after_json"]), "duplicate": True, "undone": bool(existing["undone"])}
    if not card or card.get("unanswered"):
        raise ValueError("This question is unavailable or has no answer")
    if card["revision"] != value["revision"]:
        raise ValueError("The answer changed. Refresh this question before rating it.")
    previous = con.execute("SELECT * FROM study_cards WHERE id=?", (card["id"],)).fetchone()
    previous = dict(previous) if previous else None
    version = previous["version"] if previous else 0
    if value.get("version") != version:
        raise ValueError("This question was updated in another tab. Refresh before rating it.")
    rating, mode = value.get("rating"), value.get("mode")
    if type(rating) is not int or rating not in (1, 2, 3, 4) or mode not in ("planned", "practice"):
        raise ValueError("Choose a rating and study mode")
    scheduled = mode == "planned" or value.get("schedule") is True
    schedule = previous["schedule_json"] if previous else None
    if mode == "planned":
        prefs = con.execute("SELECT value_json FROM study_state WHERE key='preferences'").fetchone()
        settings = {**DEFAULTS, **(json.loads(prefs[0]) if prefs else {})}
        if not _eligible(card, settings):
            raise ValueError("Add this source to your review plan first")
        offset = timedelta(minutes=settings["timezone_offset"])
        start = (now + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
        end = start + timedelta(days=1)
        today = {r[0] for r in con.execute(
            "SELECT DISTINCT card_id FROM study_reviews WHERE undone=0 AND scheduled=1 "
            "AND julianday(reviewed_at)>=julianday(?) AND julianday(reviewed_at)<julianday(?)",
            (start.isoformat(), end.isoformat()))}
        if card["id"] not in today and len(today) >= settings["daily_limit"]:
            raise ValueError("Today's plan is complete. You can continue in free practice.")
    if scheduled:
        from fsrs import Card, Rating, Scheduler
        scheduler = Scheduler(enable_fuzzing=False)
        fsrs_card = (Card.from_json(schedule) if schedule and previous["revision"] == card["revision"]
                     else Card(card_id=int(card["id"][:12], 16), due=now))
        fsrs_card, _log = scheduler.review_card(fsrs_card, Rating(rating), review_datetime=now)
        schedule = fsrs_card.to_json()
    row = {"id": card["id"], "revision": previous["revision"] if previous and not scheduled else card["revision"], "page": card["rel"], "data_json": _json(card),
           "schedule_json": schedule, "version": version + 1, "updated_at": now.isoformat()}
    _put_card(con, row)
    con.execute("INSERT INTO study_reviews(event_id,card_id,reviewed_at,rating,mode,scheduled,revision,previous_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (event_id, card["id"], now.isoformat(), rating, mode, int(scheduled), card["revision"],
                 _json(previous) if previous else None, _json(row)))
    return {"event_id": event_id, "card": row, "due": json.loads(schedule)["due"] if schedule else None}


def _undo(con, value, now):
    event = con.execute("SELECT * FROM study_reviews WHERE event_id=?", (value["event_id"],)).fetchone()
    if not event:
        raise ValueError("Review event not found")
    if event["undone"]:
        return {"ok": True}
    latest = con.execute("SELECT event_id FROM study_reviews WHERE card_id=? AND undone=0 ORDER BY seq DESC LIMIT 1", (event["card_id"],)).fetchone()
    row = con.execute("SELECT * FROM study_cards WHERE id=?", (event["card_id"],)).fetchone()
    if latest[0] != event["event_id"] or row["version"] != json.loads(event["after_json"])["version"]:
        raise ValueError("Only the latest unchanged review can be undone")
    prior = json.loads(event["previous_json"]) if event["previous_json"] else {**dict(row), "schedule_json": None}
    prior["version"], prior["updated_at"] = row["version"] + 1, now.isoformat()
    _put_card(con, prior)
    con.execute("UPDATE study_reviews SET undone=1 WHERE event_id=?", (event["event_id"],))
    return {"ok": True}


def _note(con, root, value, now, flag=False):
    import hashlib
    from reader_data import published_page
    file, meta, _body = published_page(root, value["page"])
    revision = hashlib.sha256(file.read_bytes()).hexdigest()
    if value.get("revision") != revision:
        raise ValueError("The knowledge page changed. Refresh it before saving this selection.")
    identity = str(uuid.UUID(value["id"])) if value.get("id") else str(uuid.uuid4())
    previous = con.execute("SELECT * FROM study_notes WHERE id=?", (identity,)).fetchone()
    if previous and previous["version"] != value.get("version"):
        saved = json.loads(previous["data_json"])
        if flag and saved.get("kind") == "question" and all(saved.get(k) == value.get(k) for k in ("page", "revision", "quote", "text", "source", "source_page")):
            return {**saved, "version": previous["version"]}
        raise ValueError("This annotation changed in another tab")
    quote = str(value.get("quote", ""))[:20000]
    note = str(value.get("text", "")).strip()[:20000]
    if not quote and not note:
        raise ValueError("Select text or enter a note")
    if flag and not note:
        raise ValueError("Describe your question")
    data = {k: value[k] for k in ("prefix", "suffix", "start", "end", "source", "source_page") if k in value}
    data.update({"id": identity, "page": value["page"], "revision": revision, "quote": quote, "text": note,
                 "color": value.get("color", "yellow"), "kind": "question" if flag else "annotation",
                 "at": now.isoformat()})
    if flag:
        refs = site_exporter._source_refs(meta.get("source_refs"))
        if meta.get("type") == "source" and meta.get("source_id"):
            refs.append(meta["source_id"])
        source = data.get("source")
        if source not in refs:
            raise ValueError("Choose a source cited by this knowledge page")
        if data.get("source_page") is not None:
            from reader_data import source_info
            if type(data["source_page"]) is not int or not 1 <= data["source_page"] <= source_info(root, source)["pages"]:
                raise ValueError("Source page is outside the available range")
        cursor = con.execute("INSERT INTO review_proposals(source_id,target_path,kind,diff_path,reason,created_at,status) VALUES (?,?,?,NULL,?,?,?)",
                             (source, value["page"], "reader-question", _json(data), now.isoformat(), "open"))
        data["proposal_id"] = cursor.lastrowid
    version = previous["version"] + 1 if previous else 1
    con.execute("INSERT OR REPLACE INTO study_notes VALUES (?,?,?,?,?,0)",
                (identity, value["page"], _json(data), version, now.isoformat()))
    return {**data, "version": version}


def _backup(value):
    if value.get("format") != "study-kb-personal" or value.get("version") != 1 or set(value.get("tables", {})) != set(TABLES):
        raise ValueError("Unsupported personal-study backup")
    tables = value["tables"]
    for table, rows in tables.items():
        if not isinstance(rows, list) or len({r[KEYS[table]] for r in rows}) != len(rows):
            raise ValueError("Duplicate or invalid backup records")
        for row in rows:
            for key in ("value_json", "data_json", "schedule_json", "previous_json", "after_json"):
                if row.get(key) is not None:
                    json.loads(row[key])
    for row in tables["study_state"]:
        if row["key"] == "preferences":
            _preferences(json.loads(row["value_json"]))
        elif row["key"] != "practice-session" and not row["key"].startswith("reading:"):
            raise ValueError("Backup contains non-personal application state")
    card_ids = {r["id"] for r in tables["study_cards"]}
    for row in tables["study_cards"]:
        if type(row["version"]) is not int or row["version"] < 1 or not isinstance(json.loads(row["data_json"]), dict):
            raise ValueError("Invalid card record")
        if row["schedule_json"]:
            from fsrs import Card
            Card.from_json(row["schedule_json"])
    for row in tables["study_reviews"]:
        uuid.UUID(row["event_id"])
        if row["card_id"] not in card_ids or row["rating"] not in (1, 2, 3, 4) or row["mode"] not in ("planned", "practice"):
            raise ValueError("Invalid review event")
        if json.loads(row["after_json"])["id"] != row["card_id"]:
            raise ValueError("Review event refers to a different card")
    return tables


def _import(con, value):
    tables = _backup(value["backup"])
    conflicts, additions, unchanged, unchanged_keys = [], 0, 0, set()
    for table, rows in tables.items():
        columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for row in rows:
            if set(row) != columns:
                raise ValueError(f"Invalid columns in {table}")
            existing = con.execute(f"SELECT * FROM {table} WHERE {KEYS[table]}=?", (row[KEYS[table]],)).fetchone()
            ignored = ("seq", "version") if table == "study_cards" else ("seq",)
            compare = {k: v for k, v in row.items() if k not in ignored}
            if not existing:
                additions += 1
            elif compare != {k: v for k, v in dict(existing).items() if k not in ignored}:
                conflicts.append({"table": table, "id": row[KEYS[table]]})
            else:
                unchanged += 1
                unchanged_keys.add((table, row[KEYS[table]]))
    conflicting_cards = {r["id"] for r in conflicts if r["table"] == "study_cards"}
    incoming_events = {r["event_id"] for r in tables["study_reviews"]}
    replaced_events = sum(1 for r in con.execute("SELECT card_id,event_id FROM study_reviews") if r["card_id"] in conflicting_cards and r["event_id"] not in incoming_events)
    result = {"additions": additions, "conflicts": conflicts, "unchanged": unchanged, "replaced_history": replaced_events}
    if not value.get("apply"):
        return result
    if value.get("policy") not in ("keep", "replace"):
        raise ValueError("Choose whether to keep existing records or replace conflicts")
    if value["policy"] == "replace" and conflicts and value.get("confirm") != "replace":
        raise ValueError("Confirm replacing conflicting personal records")
    # A card and its event history are imported as a group. Keeping a conflicting
    # card also keeps its local history, preventing an imported undo from changing it.
    keep_cards = {r["id"] for r in conflicts if r["table"] == "study_cards" and value["policy"] == "keep"}
    if value["policy"] == "replace":
        for identity in conflicting_cards:
            con.execute("DELETE FROM study_reviews WHERE card_id=?", (identity,))
    for table, rows in tables.items():
        for row in rows:
            if (table, row[KEYS[table]]) in unchanged_keys and not (table == "study_reviews" and value["policy"] == "replace" and row["card_id"] in conflicting_cards):
                continue
            if table == "study_reviews" and row["card_id"] in keep_cards:
                continue
            data = {k: v for k, v in row.items() if k != "seq"}
            if table == "study_cards" and value["policy"] == "replace" and row["id"] in conflicting_cards:
                version = con.execute("SELECT version FROM study_cards WHERE id=?", (row["id"],)).fetchone()[0]
                data["version"] = max(version, data["version"]) + 1
            fields = list(data)
            conflict_sql = "DO NOTHING" if value["policy"] == "keep" else "DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in fields if k != KEYS[table])
            con.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) ON CONFLICT({KEYS[table]}) {conflict_sql}", tuple(data.values()))
    return {**result, "applied": True}


def write(root, action, value, *, now=None):
    now = _now(now)
    # Build the question before acquiring SQLite's write lock. Rendering the
    # catalogue must not block unrelated progress/annotation writes.
    card = next((c for c in _catalogue(root) if c["id"] == value["id"]), None) if action == "review" else None
    db = _db(root)
    state_store.init_db(db)
    con = state_store.connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        if action == "review":
            result = _review(con, card, value, now)
        elif action == "undo":
            result = _undo(con, value, now)
        elif action == "preferences":
            old = con.execute("SELECT value_json FROM study_state WHERE key='preferences'").fetchone()
            result = _preferences({**(json.loads(old[0]) if old else {}), **value})
            con.execute("INSERT OR REPLACE INTO study_state VALUES (?,?,?)", ("preferences", _json(result), now.isoformat()))
        elif action in ("progress", "session"):
            if action == "progress":
                from reader_data import published_page
                published_page(root, value["page"])
            key = "reading:" + value["page"] if action == "progress" else "practice-session"
            result = {**value, "at": now.isoformat()}
            con.execute("INSERT OR REPLACE INTO study_state VALUES (?,?,?)", (key, _json(result), now.isoformat()))
        elif action in ("annotation", "flag"):
            result = _note(con, root, value, now, action == "flag")
        elif action == "delete-annotation":
            changed = con.execute("UPDATE study_notes SET deleted=1, version=version+1, updated_at=? WHERE id=? AND version=?",
                                  (now.isoformat(), value["id"], value["version"])).rowcount
            if not changed:
                raise ValueError("This annotation changed. Refresh before removing it.")
            result = {"ok": True}
        elif action == "import":
            result = _import(con, value)
        elif action == "proposal-decision":
            item = con.execute("SELECT * FROM review_proposals WHERE id=?", (value["id"],)).fetchone()
            if not item or item["status"] != value.get("expected_status"):
                raise ValueError("This pending item changed. Refresh it first.")
            decision, reason = value.get("decision"), str(value.get("reason", "")).strip()
            if decision not in ("resolved", "rejected", "deferred") or not reason:
                raise ValueError("Choose a decision and explain the review result")
            if decision == "resolved" and value.get("verified") is not True:
                raise ValueError("Verify this individual finding before closing it")
            result = {"decision": decision, "reason": reason, "at": now.isoformat()}
            from content_review import record_decision
            record_decision(con, item["id"], decision, reason, now.isoformat())
            con.execute("UPDATE review_proposals SET status=? WHERE id=?",
                        ("open" if decision == "deferred" else decision, item["id"]))
            con.execute("INSERT OR REPLACE INTO study_state VALUES (?,?,?)",
                        ("proposal:" + str(item["id"]), _json(result), now.isoformat()))
        else:
            raise ValueError("Unknown study write action")
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
