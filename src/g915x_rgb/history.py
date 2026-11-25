"""Undo/redo stack for profile edits.

Holds a per-profile snapshot list capped at 50 entries. The snapshot is a
JSON-serializable copy of the profile so undo/redo doesn't share mutable
references with the live editor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import Profile

MAX_HISTORY = 50


@dataclass
class _Stack:
    undo: list[dict] = field(default_factory=list)
    redo: list[dict] = field(default_factory=list)


class History:
    """Per-profile undo/redo, keyed by saved-path or fallback name."""

    def __init__(self) -> None:
        self._stacks: dict[str, _Stack] = {}

    def _key(self, profile: Profile) -> str:
        if profile._saved_path is not None:
            return str(profile._saved_path)
        return f"name:{profile.name}"

    def _stack(self, profile: Profile) -> _Stack:
        return self._stacks.setdefault(self._key(profile), _Stack())

    def push(self, profile: Profile) -> None:
        """Capture the current state of ``profile`` as an undo point."""
        s = self._stack(profile)
        snap = profile.to_dict()
        if s.undo and s.undo[-1] == snap:
            return
        s.undo.append(snap)
        if len(s.undo) > MAX_HISTORY:
            s.undo.pop(0)
        s.redo.clear()

    def can_undo(self, profile: Profile) -> bool:
        # We need at least two entries: the current state plus a prior one
        # to roll back to.
        return len(self._stack(profile).undo) >= 2

    def can_redo(self, profile: Profile) -> bool:
        return bool(self._stack(profile).redo)

    def undo(self, profile: Profile) -> dict | None:
        """Return the prior snapshot dict, or None if nothing to undo."""
        s = self._stack(profile)
        if len(s.undo) < 2:
            return None
        current = s.undo.pop()
        s.redo.append(current)
        return s.undo[-1]

    def redo(self, profile: Profile) -> dict | None:
        s = self._stack(profile)
        if not s.redo:
            return None
        snap = s.redo.pop()
        s.undo.append(snap)
        return snap

    def reset(self, profile: Profile) -> None:
        self._stacks.pop(self._key(profile), None)
