from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, payload: Any) -> None:
        path = self.output_dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        tmp.replace(path)

    def append_jsonl(self, name: str, payload: Any) -> None:
        with (self.output_dir / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
