"""Run state persistence for the book-level pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


IN_PROGRESS_STATUSES = {"authoring", "validating", "reviewing", "publishing"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class SectionState:
    section_id: str
    status: str
    current_attempt: int = 0
    max_attempt: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "status": self.status,
            "current_attempt": self.current_attempt,
            "max_attempt": self.max_attempt,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionState":
        return cls(
            section_id=data["section_id"],
            status=data.get("status", "not_started"),
            current_attempt=int(data.get("current_attempt", 0)),
            max_attempt=int(data.get("max_attempt", 0)),
            history=list(data.get("history") or []),
        )


@dataclass
class RunState:
    run_id: str
    book_id: str
    created_at: str
    updated_at: str
    status: str
    executor: str
    config: dict[str, Any]
    stages: dict[str, Any]
    progress: dict[str, int]
    section_states: dict[str, SectionState]
    run_dir: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "book_id": self.book_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "executor": self.executor,
            "config": self.config,
            "stages": self.stages,
            "progress": self.progress,
        }


class RunStateManager:
    def __init__(self, book_root: Path):
        self.book_root = Path(book_root)
        self.runs_root = self.book_root / "pipeline-workspace" / "runs"

    def create_run(self, book_id: str, config: dict[str, Any],
                   sections: list[dict[str, Any]]) -> RunState:
        run_id = self._new_run_id()
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "sections").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)

        max_attempt = int(config.get("max_revision_retry", 0))
        section_states = {}
        for section in sections:
            section_id = section["id"]
            status = self._status_from_manifest(section)
            section_states[section_id] = SectionState(
                section_id=section_id,
                status=status,
                current_attempt=0,
                max_attempt=max_attempt,
            )

        created_at = _now()
        run_state = RunState(
            run_id=run_id,
            book_id=book_id,
            created_at=created_at,
            updated_at=created_at,
            status="running",
            executor=config.get("executor", "claude-code-queue"),
            config=config,
            stages={},
            progress={},
            section_states=section_states,
            run_dir=run_dir,
        )
        run_state.progress = self.calculate_progress(run_state)
        self.save(run_state)
        return run_state

    def load_latest_run(self, book_root: Path | None = None) -> RunState | None:
        runs_root = (Path(book_root) / "pipeline-workspace" / "runs") if book_root else self.runs_root
        if not runs_root.exists():
            return None
        candidates = sorted(
            p for p in runs_root.iterdir()
            if p.is_dir() and (p / "run-state.yaml").exists()
        )
        if not candidates:
            return None
        return self.load_run(candidates[-1].name, runs_root.parent.parent)

    def load_run(self, run_id: str, book_root: Path | None = None) -> RunState:
        root = Path(book_root) if book_root else self.book_root
        run_dir = root / "pipeline-workspace" / "runs" / run_id
        with open(run_dir / "run-state.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        section_states = {}
        sections_dir = run_dir / "sections"
        for path in sorted(sections_dir.glob("*.yaml")):
            with open(path, "r", encoding="utf-8") as f:
                section_data = yaml.safe_load(f) or {}
            state = SectionState.from_dict(section_data)
            section_states[state.section_id] = state

        return RunState(
            run_id=data["run_id"],
            book_id=data["book_id"],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            status=data.get("status", "running"),
            executor=data.get("executor", "claude-code-queue"),
            config=data.get("config") or {},
            stages=data.get("stages") or {},
            progress=data.get("progress") or {},
            section_states=section_states,
            run_dir=run_dir,
        )

    def update_stage(self, run_state: RunState, stage_name: str, status: str, **kwargs):
        entry = {"status": status, "updated_at": _now()}
        if status == "completed":
            entry["completed_at"] = entry["updated_at"]
        entry.update(kwargs)
        run_state.stages[stage_name] = entry
        self.save(run_state)

    def update_section(self, run_state: RunState, section_id: str,
                       action: str, result: str, **kwargs):
        state = run_state.section_states[section_id]
        if result in {
            "not_started", "authoring", "validating", "reviewing", "reviewed",
            "publishing", "published", "failed", "needs_human_review",
        }:
            state.status = result
        if result == "failed" or action in {"author", "revise"}:
            state.current_attempt += 1
        history_item = {
            "at": _now(),
            "action": action,
            "result": result,
        }
        history_item.update(kwargs)
        state.history.append(history_item)
        run_state.progress = self.calculate_progress(run_state)
        self.save(run_state)

    def sync_with_manifest(self, run_state: RunState, sections: list[dict[str, Any]]):
        """Refresh stable section states from the current manifest.

        The manifest remains the source of truth for states produced by
        deterministic pipeline steps, such as reviewed/published. Retry attempt
        counts stay in run-state.
        """
        max_attempt = int(run_state.config.get("max_revision_retry", 0))
        seen = set()
        for section in sections:
            section_id = section["id"]
            seen.add(section_id)
            target_status = self._status_from_manifest(section)
            state = run_state.section_states.get(section_id)
            if state is None:
                run_state.section_states[section_id] = SectionState(
                    section_id=section_id,
                    status=target_status,
                    current_attempt=0,
                    max_attempt=max_attempt,
                )
                continue

            state.max_attempt = max_attempt
            if target_status in {"published", "reviewed", "needs_human_review"}:
                state.status = target_status
            elif target_status == "failed" and state.status not in IN_PROGRESS_STATUSES:
                state.status = "failed"
            elif target_status == "not_started" and state.status in {
                "reviewed", "published", "needs_human_review",
            }:
                state.status = "not_started"

        for section_id in list(run_state.section_states):
            if section_id not in seen:
                del run_state.section_states[section_id]

        run_state.progress = self.calculate_progress(run_state)
        self.save(run_state)

    def get_next_sections(self, run_state: RunState, batch_size: int) -> list[SectionState]:
        selected = []
        for state in run_state.section_states.values():
            if state.status in {"published", "needs_human_review"}:
                continue
            if state.status in IN_PROGRESS_STATUSES:
                state.status = "not_started"
            if state.status == "failed" and state.current_attempt >= state.max_attempt:
                continue
            if state.status in {"not_started", "failed"}:
                selected.append(state)
            if len(selected) >= batch_size:
                break
        run_state.progress = self.calculate_progress(run_state)
        self.save(run_state)
        return selected

    def calculate_progress(self, run_state: RunState) -> dict[str, int]:
        counts = {
            "total": len(run_state.section_states),
            "published": 0,
            "not_started": 0,
            "failed": 0,
            "needs_human_review": 0,
            "reviewed": 0,
            "in_progress": 0,
        }
        for state in run_state.section_states.values():
            if state.status in counts:
                counts[state.status] += 1
            elif state.status in IN_PROGRESS_STATUSES:
                counts["in_progress"] += 1
        return counts

    def finalize(self, run_state: RunState, status: str = "completed"):
        run_state.status = status
        self.save(run_state)

    def save(self, run_state: RunState):
        run_state.updated_at = _now()
        run_state.progress = self.calculate_progress(run_state)
        run_state.run_dir.mkdir(parents=True, exist_ok=True)
        (run_state.run_dir / "sections").mkdir(parents=True, exist_ok=True)

        with open(run_state.run_dir / "run-state.yaml", "w", encoding="utf-8") as f:
            yaml.dump(run_state.to_dict(), f, allow_unicode=True, sort_keys=False)

        for state in run_state.section_states.values():
            path = run_state.run_dir / "sections" / f"{state.section_id}.yaml"
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(state.to_dict(), f, allow_unicode=True, sort_keys=False)

    def _new_run_id(self) -> str:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        base = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = base
        suffix = 1
        while (self.runs_root / run_id).exists():
            suffix += 1
            run_id = f"{base}-{suffix:02d}"
        return run_id

    def _status_from_manifest(self, section: dict[str, Any]) -> str:
        if (
            section.get("status") == "published"
            or section.get("publish_status") == "published"
        ):
            return "published"
        status = section.get("status", "registered")
        if status in {
            "reviewed", "failed", "needs_human_review",
            "authoring", "validating", "reviewing", "publishing",
        }:
            return status
        return "not_started"
