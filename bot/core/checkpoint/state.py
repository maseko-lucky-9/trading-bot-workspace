"""
BotState dataclass + CheckpointManager (US-009).
"""
from __future__ import annotations

import json
import pickle
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BotState:
    iteration: int = 0
    positions: list = field(default_factory=list)
    performance_summary: dict = field(default_factory=dict)
    strategy_params: dict = field(default_factory=dict)
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    day_start_date: str = ""   # ISO date "YYYY-MM-DD" of last day-equity reset
    cooling_off_until: float = 0.0   # epoch; 0.0 = not in cooling-off
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


class CheckpointManager:
    def __init__(self, checkpoint_dir: Path | None = None) -> None:
        bot_root = Path(__file__).resolve().parents[2]
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else (
            bot_root / "checkpoints"
        )
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _stamp(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")

    def save(self, state: BotState) -> Path:
        stamp = self._stamp()
        pkl = self.checkpoint_dir / f"state_{stamp}.pkl"
        manifest = self.checkpoint_dir / f"state_{stamp}.json"
        with pkl.open("wb") as f:
            pickle.dump(state, f)
        manifest.write_text(json.dumps(state.to_dict(), default=str, indent=2))
        return pkl

    def list_checkpoints(self) -> list[dict]:
        out = []
        for p in sorted(self.checkpoint_dir.glob("state_*.pkl")):
            out.append({"path": str(p), "name": p.name, "size": p.stat().st_size})
        return out

    def load(self, path: Path | None = None) -> BotState | None:
        if path is None:
            ckpts = sorted(self.checkpoint_dir.glob("state_*.pkl"))
            if not ckpts:
                return None
            path = ckpts[-1]
        with Path(path).open("rb") as f:
            return pickle.load(f)

    def rotate(self, keep: int = 10) -> int:
        ckpts = sorted(self.checkpoint_dir.glob("state_*.pkl"))
        deleted = 0
        for p in ckpts[:-keep]:
            try:
                p.unlink()
                manifest = p.with_suffix(".json")
                if manifest.exists():
                    manifest.unlink()
                deleted += 1
            except Exception:
                pass
        return deleted
