"""Prompt loader (Ch. 15).

Prompts are plain ``.txt`` files with ``{placeholder}`` tokens. Rendering uses an explicit,
named replace (NOT ``str.format``) so the literal JSON braces inside the prompts are left intact.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt by logical name, e.g. ``"script_generator.system"`` -> ``*.txt``."""
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}' not found at {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, /, **values: object) -> str:
    """Replace only the supplied ``{key}`` tokens; leave all other braces untouched."""
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


__all__ = ["PROMPTS_DIR", "load_prompt", "render_prompt"]
