"""Builds the baseline system prompt from its parts.

The baseline prompt is the three multi-agent prompts (Intake, Planner,
Checker) combined, plus the shared plan conventions, with no extra hints
(TASKS.md §2). Each part is one file in ``prompts/`` so the final multi-agent
prompts can replace the drafts one for one at the prompt freeze.

HTML comments (``<!-- ... -->``) in the files are notes for the team and are
removed before the prompt is sent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
SECTIONS = ("intake", "planner", "checker", "output_rules")
_COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)


@dataclass(frozen=True)
class BaselinePrompt:
    text: str
    sha256: str
    drafts: tuple[str, ...]  # sections still marked DRAFT

    @property
    def version(self) -> str:
        return self.sha256[:12]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_system_prompt(prompt_dir: Path = PROMPT_DIR) -> BaselinePrompt:
    template = _COMMENT.sub("", _read(prompt_dir / "baseline.md")).strip()
    parts, drafts = {}, []
    for name in SECTIONS:
        raw = _read(prompt_dir / f"{name}.md")
        if "DRAFT" in raw.split("-->", 1)[0] and raw.lstrip().startswith("<!--"):
            drafts.append(name)
        parts[name] = _COMMENT.sub("", raw).strip()
    text = template.format(**parts)
    return BaselinePrompt(text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), drafts=tuple(drafts))


def build_user_message(bundle_id: str, document_list: str, planning_start: date | None) -> str:
    """The per-run message. Kept out of the system prompt so the system prompt stays cacheable."""
    if planning_start is not None:
        start = f"Planning start: {planning_start.isoformat()} (the first day work may be scheduled)."
    else:
        start = (
            "Planning start: not given. Use the planning or report date stated in the documents; if there is "
            "none, use the next working day after the latest document date, and list this as an assumption."
        )
    return (
        f"Bundle id: {bundle_id}\n"
        f"{start}\n\n"
        f"Documents in the bundle:\n{document_list}\n\n"
        "Build the work order plan for this bundle and submit it."
    )
