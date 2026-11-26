"""Programmatic Motion-Graphics Engine: TemplateRegistry + 7 Dark-Mode Templates.

Renders deterministic 1080p animated HTML/CSS/SVG motion graphics via headless Chromium
(Playwright) piped directly into FFmpeg with exact resolution, FPS, SAR=1, and yuv420p parity.
Zero external network/font calls: every template is self-contained offline HTML/CSS/SVG/JS.
"""

from __future__ import annotations

import atexit
import contextlib
import html
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import MOTION_TEMPLATE_IDS
from ..errors import RenderError
from ..logging import get_logger
from ..providers.render_backend import _encoder_opts, _select_encoder, resolve_ffmpeg

_log = get_logger(component="motion_graphics")

# Bottom-right YouTube timestamp overlay safe zone as fractions of (width, height).
# On 1920x1080 this reserves x >= 1612, y >= 950 completely clear of cards, text, and badges.
YT_SAFE_X_FRAC = 0.84
YT_SAFE_Y_FRAC = 0.88


def clamp_text(value: Any, max_chars: int, *, preserve_newlines: bool = False) -> str:
    """Coerce and word-boundary clamp ``value`` to ``max_chars`` characters."""
    if value is None:
        return ""
    raw = str(value)
    if preserve_newlines:
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        text = "\n".join(line for line in lines if line).strip()
    else:
        text = " ".join(raw.split()).strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    budget = max_chars - 3
    cut = text[:budget].rstrip()
    last_space = cut.rfind(" ")
    if last_space >= max(8, budget // 2):
        cut = cut[:last_space].rstrip(" ,;:-")
    return f"{cut}..."


def clamp_words(value: Any, max_words: int, max_chars: int = 120) -> str:
    """Keep at most ``max_words`` words and ``max_chars`` characters."""
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    if len(words) > max_words:
        cleaned = " ".join(words[:max_words]).rstrip(",;:-")
    return clamp_text(cleaned, max_chars)


# --------------------------------------------------------------------------- syntax highlighting
_SYNTAX_TOKEN_RE = re.compile(
    r"(#[^\n]*|//[^\n]*)"  # comment
    r"|(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"  # string
    r"|\b(def|class|return|if|elif|else|for|while|in|import|from|as|async|await|try|except|"
    r"raise|with|lambda|yield|True|False|None|const|let|var|function|interface|type|export|"
    r"SELECT|FROM|WHERE|JOIN|GROUP|BY|ORDER|LIMIT|INSERT|UPDATE|AND|OR|NOT|NULL)\b"  # keyword
    r"|\b(\d+(?:\.\d+)?(?:ms|s|x|%)?)\b"  # number
    r"|\b([A-Z][A-Za-z0-9_]{2,})\b"  # PascalCase type/class
)


def _highlight_code_line(line: str) -> str:
    """Deterministic, HTML-safe syntax highlighter for a single line of code."""
    if not line:
        return "&nbsp;"
    leading = len(line) - len(line.lstrip(" "))
    indent_html = "&nbsp;" * min(leading, 24)
    rest = line[leading:]
    out: list[str] = [indent_html]
    pos = 0
    for m in _SYNTAX_TOKEN_RE.finditer(rest):
        if m.start() > pos:
            out.append(html.escape(rest[pos : m.start()]))
        comment, string_lit, kw, num, pascal = m.groups()
        tok = html.escape(m.group(0))
        if comment is not None:
            out.append(f'<span class="tok-com">{tok}</span>')
        elif string_lit is not None:
            out.append(f'<span class="tok-str">{tok}</span>')
        elif kw is not None:
            out.append(f'<span class="tok-kw">{tok}</span>')
        elif num is not None:
            out.append(f'<span class="tok-num">{tok}</span>')
        elif pascal is not None:
            out.append(f'<span class="tok-typ">{tok}</span>')
        pos = m.end()
    if pos < len(rest):
        out.append(html.escape(rest[pos:]))
    return "".join(out)


_PLACEHOLDER_SOURCES = frozenset(
    {
        "null",
        "none",
        "n/a",
        "na",
        "unknown",
        "unspecified",
        "creator",
        "research",
        "search",
        "source",
    }
)
_POS_POLARITY_RE = re.compile(
    r"\b(?:protected|shielded|safe|retained|verified|reality|high-signal|advantage|gain|"
    r"strong|winner|optimal|pass|hire|stay|benefit|upside|keep|valid|proven|resilient)\b",
    re.IGNORECASE,
)
_NEG_POLARITY_RE = re.compile(
    r"\b(?:targeted|exposed|trap|myth|failure|fail|reject|downlevel|cut|layoff|restructuring|"
    r"risk|at\s+risk|broken|legacy|unverified|wrong|loss|downside|vulnerable|penalty|weak)\b",
    re.IGNORECASE,
)
_NEU_POLARITY_RE = re.compile(
    r"\b(?:neutral|mixed|hold|context|info|note|option|tier|phase|region)\b",
    re.IGNORECASE,
)


def _clean_source_prop(value: Any, max_chars: int) -> str:
    """Normalize optional ``source`` prop; placeholder tokens map to ``""`` (hides ``#source-badge``)."""
    txt = clamp_text(value or "", min(max_chars, 72))
    if not txt or txt.strip().lower() in _PLACEHOLDER_SOURCES:
        return ""
    return txt


def _normalize_polarity(raw_polarity: Any, text: str = "", *, fallback: str = "neutral") -> str:
    """Resolve ``"positive" | "negative" | "neutral"`` from explicit prop or semantic cues."""
    pol = str(raw_polarity or "").strip().lower()
    if pol in ("positive", "negative", "neutral"):
        return pol
    pos_hit = bool(_POS_POLARITY_RE.search(text or ""))
    neg_hit = bool(_NEG_POLARITY_RE.search(text or ""))
    if pos_hit and not neg_hit:
        return "positive"
    if neg_hit and not pos_hit:
        return "negative"
    if _NEU_POLARITY_RE.search(text or ""):
        return "neutral"
    return fallback if fallback in ("positive", "negative", "neutral") else "neutral"


def _polarity_tone(polarity: str) -> str:
    if polarity == "positive":
        return "emerald"
    if polarity == "negative":
        return "crimson"
    return "sky"


# --------------------------------------------------------------------------- prop normalization
def validate_template_props(
    template_id: str, props: dict[str, Any] | None, *, max_chars: int = 120
) -> dict[str, Any]:
    """Validate, fill safe defaults, and clamp all text fields for ``template_id``."""
    if template_id not in MOTION_TEMPLATE_IDS:
        raise ValueError(f"Unsupported template_id: {template_id!r}")
    raw = dict(props or {})

    if template_id == "big_stat_card":
        val = clamp_text(raw.get("value") or raw.get("stat") or "3.2x", min(max_chars, 24))
        label = clamp_text(
            raw.get("label") or raw.get("headline") or "Key Engineering Signal",
            max_chars,
        )
        sublabel = clamp_text(raw.get("sublabel") or raw.get("detail") or "", max_chars)
        trend = clamp_text(raw.get("trend") or "", min(max_chars, 36))
        accent = str(raw.get("accent") or "").strip().lower()
        if accent not in ("emerald", "crimson", "amber", "sky"):
            accent = (
                "crimson"
                if val.startswith("-")
                else ("emerald" if val.startswith(("+", "$")) else "sky")
            )
        source = _clean_source_prop(raw.get("source"), max_chars)
        eyebrow = clamp_text(raw.get("eyebrow") or "KEY METRIC", 36)
        return {
            "eyebrow": eyebrow,
            "value": val,
            "label": label,
            "sublabel": sublabel,
            "trend": trend,
            "accent": accent,
            "source": source,
        }

    if template_id == "evidence_card":
        pub = clamp_text(
            raw.get("publication") or raw.get("outlet") or "VERIFIED SOURCE BRIEF",
            min(max_chars, 64),
        )
        date_hdr = clamp_text(
            raw.get("date_header") or raw.get("date") or "VERIFIED EXCERPT",
            min(max_chars, 48),
        )
        excerpt = clamp_text(
            raw.get("excerpt")
            or raw.get("quote")
            or "Verified primary data confirms measurable operational impact.",
            max_chars,
        )
        hl = clamp_text(raw.get("highlight_phrase") or raw.get("highlight") or "", max_chars)
        if not hl or hl.lower() not in excerpt.lower():
            # Deterministically highlight the most salient numeric or middle phrase inside excerpt.
            words = excerpt.split()
            stat_idx = next(
                (i for i, w in enumerate(words) if any(ch.isdigit() for ch in w)),
                None,
            )
            if stat_idx is not None:
                lo = max(0, stat_idx - 1)
                hi = min(len(words), stat_idx + 4)
                hl = " ".join(words[lo:hi])
            elif len(words) >= 4:
                hl = " ".join(words[: min(6, len(words))])
            else:
                hl = excerpt
        # Strictly optional: never copy fallback `publication` into `source` when unverified.
        source = _clean_source_prop(raw.get("source"), max_chars)
        tag = clamp_text(raw.get("tag") or "PRIMARY EVIDENCE", 32)
        return {
            "publication": pub,
            "date_header": date_hdr,
            "excerpt": excerpt,
            "highlight_phrase": hl,
            "tag": tag,
            "source": source,
        }

    if template_id == "split_comparison":
        title = clamp_text(raw.get("title") or "SIDE-BY-SIDE CONTRAST", min(max_chars, 72))
        left_h = clamp_text(
            raw.get("left_header") or "BASELINE / COMMON ASSUMPTION", min(max_chars, 44)
        )
        right_h = clamp_text(raw.get("right_header") or "ACTUAL MECHANISM", min(max_chars, 44))
        raw_l = raw.get("left_items") or raw.get("left") or ["Conventional assumption"]
        raw_r = raw.get("right_items") or raw.get("right") or ["Measured operational reality"]
        if isinstance(raw_l, str):
            raw_l = [raw_l]
        if isinstance(raw_r, str):
            raw_r = [raw_r]
        item_cap = max(28, min(max_chars, 110))
        left_items = [clamp_text(x, item_cap) for x in list(raw_l)[:4] if str(x).strip()]
        right_items = [clamp_text(x, item_cap) for x in list(raw_r)[:4] if str(x).strip()]
        if not left_items:
            left_items = ["Conventional assumption"]
        if not right_items:
            right_items = ["Measured operational reality"]
        left_blob = f"{left_h} {' '.join(left_items)}"
        right_blob = f"{right_h} {' '.join(right_items)}"
        left_pol = _normalize_polarity(raw.get("left_polarity"), left_blob, fallback="")
        right_pol = _normalize_polarity(raw.get("right_polarity"), right_blob, fallback="")
        if not left_pol and not right_pol:
            left_pol, right_pol = "negative", "positive"
        elif not left_pol:
            left_pol = (
                "negative"
                if right_pol == "positive"
                else ("positive" if right_pol == "negative" else "neutral")
            )
        elif not right_pol:
            right_pol = (
                "negative"
                if left_pol == "positive"
                else ("positive" if left_pol == "negative" else "neutral")
            )
        source = _clean_source_prop(raw.get("source"), max_chars)
        return {
            "title": title,
            "left_header": left_h,
            "right_header": right_h,
            "left_items": left_items,
            "right_items": right_items,
            "left_polarity": left_pol,
            "right_polarity": right_pol,
            "source": source,
        }

    if template_id == "rubric_checklist":
        variant = str(raw.get("variant") or "checklist").strip().lower()
        if variant not in ("checklist", "discussion_prompt"):
            variant = "checklist"
        source = _clean_source_prop(raw.get("source"), max_chars)
        if variant == "discussion_prompt":
            claim = clamp_text(
                raw.get("claim")
                or raw.get("title")
                or "Key operational trade-offs separate surface claims from real incentives.",
                max_chars,
            )
            question = clamp_words(
                raw.get("question") or "Agree or hard disagree?",
                max_words=6,
                max_chars=min(max_chars, 56),
            )
            opts_raw = raw.get("options") or ["Agree", "Disagree"]
            opts = [clamp_text(o, 36) for o in list(opts_raw)[:2] if str(o).strip()]
            return {
                "variant": "discussion_prompt",
                "eyebrow": clamp_text(raw.get("eyebrow") or "COMMENT FRICTION // YOUR CALL", 40),
                "claim": claim,
                "question": question,
                "options": opts or ["Agree", "Disagree"],
                "source": source,
            }
        eyebrow = clamp_text(raw.get("eyebrow") or "SIGNAL CHECKLIST", 36)
        title = clamp_text(raw.get("title") or "KEY DECISION FACTORS", min(max_chars, 68))
        items_in = raw.get("items") or []
        norm_items: list[dict[str, str]] = []
        for entry in list(items_in)[:4]:
            if isinstance(entry, dict):
                lbl = clamp_text(entry.get("label") or entry.get("name") or "Factor", max_chars)
                det = clamp_text(entry.get("detail") or "", max_chars)
                raw_st = str(entry.get("status") or entry.get("badge") or "").strip()
                pol = _normalize_polarity(
                    entry.get("polarity"),
                    f"{raw_st} {lbl} {det}",
                    fallback="positive",
                )
                default_tag = (
                    "AT RISK"
                    if pol == "negative"
                    else ("KEY FACTOR" if pol == "neutral" else "VERIFIED")
                )
                st = clamp_words(raw_st or default_tag, 3, 22).upper()
            else:
                lbl = clamp_text(entry, max_chars)
                det = ""
                pol = _normalize_polarity("", lbl, fallback="positive")
                st = "AT RISK" if pol == "negative" else "VERIFIED"
            tone = _polarity_tone(pol)
            if lbl:
                norm_items.append(
                    {
                        "label": lbl,
                        "detail": det,
                        "status": st,
                        "polarity": pol,
                        "tone": tone,
                    }
                )
        if len(norm_items) < 2:
            norm_items = [
                {
                    "label": "Primary Mechanism Verified",
                    "detail": "Direct operational driver backed by concrete evidence",
                    "status": "VERIFIED",
                    "polarity": "positive",
                    "tone": "emerald",
                },
                {
                    "label": "Surface Assumption Exposed",
                    "detail": "Fails under real financial or structural constraints",
                    "status": "EXPOSED",
                    "polarity": "negative",
                    "tone": "crimson",
                },
            ]
        return {
            "variant": "checklist",
            "eyebrow": eyebrow,
            "title": title,
            "items": norm_items,
            "source": source,
        }

    if template_id == "animated_chart":
        chart_type = str(raw.get("chart_type") or raw.get("type") or "bar").strip().lower()
        if chart_type not in ("line", "bar"):
            chart_type = "bar"
        title = clamp_text(raw.get("title") or "PERFORMANCE TREND", min(max_chars, 68))
        y_unit = clamp_text(raw.get("y_unit") or "", 10)
        callout_label = clamp_text(raw.get("callout_label") or "", 32)
        callout_value = clamp_text(raw.get("callout_value") or "", 24)
        source = _clean_source_prop(raw.get("source"), max_chars)

        if "items" in raw and isinstance(raw["items"], list) and raw["items"]:
            items = [x for x in raw["items"] if isinstance(x, dict)][:6]
            x_labels = [
                clamp_text(i.get("label") or f"#{idx + 1}", 22) for idx, i in enumerate(items)
            ]
            vals: list[float] = []
            hl_idx = -1
            for idx, i in enumerate(items):
                try:
                    vals.append(float(i.get("value") or 0.0))
                except (TypeError, ValueError):
                    vals.append(0.0)
                if i.get("highlight"):
                    hl_idx = idx
            series = [{"name": clamp_text(raw.get("series_name") or "Metric", 24), "values": vals}]
        else:
            raw_x = raw.get("x_labels") or ["Q1", "Q2", "Q3", "Q4"]
            x_labels = [clamp_text(x, 18) for x in list(raw_x)[:6]]
            raw_series = raw.get("series") or [{"name": "Series A", "values": [24, 38, 52, 71]}]
            series = []
            for s_idx, s in enumerate(list(raw_series)[:2]):
                if not isinstance(s, dict):
                    continue
                s_vals: list[float] = []
                for v in list(s.get("values") or [])[: len(x_labels)]:
                    try:
                        s_vals.append(float(v))
                    except (TypeError, ValueError):
                        s_vals.append(0.0)
                while len(s_vals) < len(x_labels):
                    s_vals.append(s_vals[-1] if s_vals else 0.0)
                series.append(
                    {
                        "name": clamp_text(s.get("name") or f"Series {s_idx + 1}", 24),
                        "values": s_vals,
                    }
                )
            if not series:
                series = [{"name": "Metric", "values": [20.0, 45.0, 60.0, 80.0][: len(x_labels)]}]
            hl_idx = int(raw.get("highlight_index", len(x_labels) - 1))

        if not x_labels:
            x_labels = ["T1", "T2", "T3"]
            series = [{"name": "Metric", "values": [10.0, 25.0, 40.0]}]
        return {
            "chart_type": chart_type,
            "title": title,
            "x_labels": x_labels,
            "series": series,
            "highlight_index": hl_idx,
            "y_unit": y_unit,
            "callout_label": callout_label,
            "callout_value": callout_value,
            "source": source,
        }

    if template_id == "architecture_flow":
        title = clamp_text(raw.get("title") or "SYSTEM ARCHITECTURE FLOW", min(max_chars, 68))
        subtitle = clamp_text(raw.get("subtitle") or "", max_chars)
        raw_nodes = raw.get("nodes") or []
        nodes: list[dict[str, Any]] = []
        for idx, n in enumerate(list(raw_nodes)[:6]):
            if isinstance(n, dict):
                nodes.append(
                    {
                        "tag": clamp_text(n.get("tag") or f"STAGE {idx + 1}", 20),
                        "name": clamp_text(
                            n.get("name") or n.get("label") or f"Node {idx + 1}", 34
                        ),
                        "detail": clamp_text(n.get("detail") or "", max_chars),
                        "highlight": bool(n.get("highlight")),
                    }
                )
            elif str(n).strip():
                nodes.append(
                    {
                        "tag": f"STAGE {idx + 1}",
                        "name": clamp_text(n, 34),
                        "detail": "",
                        "highlight": idx == len(raw_nodes) - 1,
                    }
                )
        if len(nodes) < 3:
            nodes = [
                {
                    "tag": "INGEST",
                    "name": "Request Gateway",
                    "detail": "Schema + auth check",
                    "highlight": False,
                },
                {
                    "tag": "CORE",
                    "name": "Retrieval Engine",
                    "detail": "Candidate ANN lookup",
                    "highlight": False,
                },
                {
                    "tag": "SERVE",
                    "name": "Heavy Ranker",
                    "detail": "P99 < 15ms SLA",
                    "highlight": True,
                },
            ]
        source = _clean_source_prop(raw.get("source"), max_chars)
        return {
            "title": title,
            "subtitle": subtitle,
            "nodes": nodes,
            "source": source,
        }

    # code_ide_mockup
    filename = clamp_text(raw.get("filename") or "pipeline_service.py", 40)
    language = clamp_text(raw.get("language") or "python", 20).lower()
    code_in = raw.get("code_lines") or raw.get("code") or ""
    if isinstance(code_in, list):
        raw_lines = [str(line) for line in code_in]
    else:
        raw_lines = str(code_in).splitlines()
    raw_lines = [line.rstrip() for line in raw_lines if line.strip() or raw_lines][:10]
    if not raw_lines:
        raw_lines = [
            "def score_candidates(batch: list[Item], budget_ms: float = 15.0):",
            "    candidates = retriever.fetch_top_k(batch, k=200)",
            "    ranked = ranker.predict(candidates, timeout_ms=budget_ms)",
            "    return [item for item in ranked if item.score >= 0.82]",
        ]
    per_line_cap = max(48, min(92, max_chars // max(len(raw_lines), 2) + 42))
    code_lines = [
        (line[: per_line_cap - 3] + "..." if len(line) > per_line_cap else line)
        for line in raw_lines[:10]
    ]
    hl_raw = raw.get("highlight_lines") or []
    hl_lines = [
        int(x)
        for x in hl_raw
        if isinstance(x, int | str) and str(x).isdigit() and 1 <= int(x) <= len(code_lines)
    ]
    if not hl_lines:
        hl_lines = [min(2, len(code_lines))]

    files_in = raw.get("files") or [filename, "config.py", "test_suite.py"]
    files = [clamp_text(f, 28) for f in list(files_in)[:5] if str(f).strip()]
    if filename not in files:
        files = [filename, *files[:4]]

    side_title = clamp_text(raw.get("side_panel_title") or "INSPECTOR", 26)
    side_in = raw.get("side_panel_items") or []
    side_items = [clamp_text(s, 64) for s in list(side_in)[:4] if str(s).strip()]
    status_text = clamp_text(raw.get("status_text") or "12/12 TESTS PASSED", 56)
    status_tone = str(raw.get("status_tone") or "pass").strip().lower()
    if status_tone not in ("pass", "fail", "info"):
        status_tone = "fail" if "FAIL" in status_text.upper() else "pass"
    source = _clean_source_prop(raw.get("source"), max_chars)
    return {
        "filename": filename,
        "language": language,
        "files": files,
        "code_lines": code_lines,
        "highlight_lines": hl_lines,
        "side_panel_title": side_title,
        "side_panel_items": side_items,
        "status_text": status_text,
        "status_tone": status_tone,
        "source": source,
    }


# --------------------------------------------------------------------------- HTML/CSS/SVG builders
_BASE_CSS = """
:root {
  --bg-deep: #090D16;
  --bg-panel: rgba(17, 24, 39, 0.88);
  --bg-panel-elev: rgba(24, 33, 52, 0.94);
  --border-subtle: rgba(148, 163, 184, 0.16);
  --fg-primary: #F8FAFC;
  --fg-secondary: #CBD5E1;
  --fg-muted: #94A3B8;
  --emerald: #10B981;
  --emerald-bg: rgba(16, 185, 129, 0.14);
  --emerald-bd: rgba(16, 185, 129, 0.44);
  --crimson: #F43F5E;
  --crimson-bg: rgba(244, 63, 94, 0.14);
  --crimson-bd: rgba(244, 63, 94, 0.44);
  --amber: #F59E0B;
  --amber-bg: rgba(245, 158, 11, 0.16);
  --amber-bd: rgba(245, 158, 11, 0.46);
  --sky: #38BDF8;
  --sky-bg: rgba(56, 189, 248, 0.14);
  --sky-bd: rgba(56, 189, 248, 0.44);
  --font-sans: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif;
  --font-mono: "Cascadia Code", Consolas, "Courier New", monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--bg-deep);
  color: var(--fg-primary);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
}
#stage {
  position: relative;
  width: __W__px;
  height: __H__px;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 18%, rgba(56, 189, 248, 0.09), transparent 46%),
    radial-gradient(circle at 82% 24%, rgba(16, 185, 129, 0.07), transparent 48%),
    linear-gradient(180deg, #0B101B 0%, #070A12 100%);
}
#grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, rgba(148, 163, 184, 0.045) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(148, 163, 184, 0.045) 1px, transparent 1px);
  background-size: 48px 48px;
  pointer-events: none;
}
#content-safe {
  position: absolute;
  top: __PAD_TOP__px;
  left: __PAD_X__px;
  right: __PAD_X__px;
  bottom: __PAD_BOTTOM__px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
}
.source-badge {
  position: absolute;
  left: __PAD_X__px;
  bottom: __SRC_BOTTOM__px;
  max-width: __SRC_MAX_W__px;
  height: 42px;
  padding: 0 18px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.24);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-mono);
  font-size: 16px;
  color: var(--fg-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.source-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--sky);
  flex-shrink: 0;
}
.eyebrow {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--sky);
}
.card-shell {
  background: var(--bg-panel);
  border: 1px solid var(--border-subtle);
  border-radius: 24px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.52);
  overflow: hidden;
}
.tok-kw { color: #FF7B72; font-weight: 600; }
.tok-str { color: #A5D6FF; }
.tok-com { color: #8B949E; font-style: italic; }
.tok-num { color: #79C0FF; }
.tok-typ { color: #D2A8FF; }
"""

_COMMON_JS = """
function clamp01(v) { return Math.max(0, Math.min(1, v)); }
function easeOutCubic(t) { t = clamp01(t); return 1 - Math.pow(1 - t, 3); }
function easeOutBack(t) {
  t = clamp01(t);
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}
function win(elapsed, start, dur) {
  return dur <= 0 ? 1 : clamp01((elapsed - start) / dur);
}
window.__fitAllText = function() {
  const nodes = document.querySelectorAll('[data-fit]');
  for (const el of nodes) {
    const minPx = parseFloat(el.getAttribute('data-min-px') || '13');
    let cur = parseFloat(window.getComputedStyle(el).fontSize);
    let guard = 0;
    while (
      guard < 140 &&
      cur > minPx &&
      (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)
    ) {
      cur -= 1;
      el.style.fontSize = cur + 'px';
      guard++;
    }
  }
};
window.__inspectLayout = function() {
  const stage = document.getElementById('stage');
  const stageW = stage.clientWidth;
  const stageH = stage.clientHeight;
  const safeX = stageW * __SAFE_X__;
  const safeY = stageH * __SAFE_Y__;
  const overflows = [];
  const checkEls = document.querySelectorAll('#stage, #content-safe, [data-check-overflow]');
  for (const el of checkEls) {
    if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) {
      overflows.push({
        id: el.id || el.className || el.tagName,
        scrollW: el.scrollWidth,
        clientW: el.clientWidth,
        scrollH: el.scrollHeight,
        clientH: el.clientHeight
      });
    }
  }
  const safeZoneViolations = [];
  const safeItems = document.querySelectorAll('[data-safe-item]');
  for (const el of safeItems) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.right > safeX && r.bottom > safeY) {
      safeZoneViolations.push({
        id: el.id || el.className || el.tagName,
        right: Math.round(r.right),
        bottom: Math.round(r.bottom),
        safeX: Math.round(safeX),
        safeY: Math.round(safeY)
      });
    }
  }
  return {
    stageWidth: stageW,
    stageHeight: stageH,
    stageScrollWidth: stage.scrollWidth,
    stageScrollHeight: stage.scrollHeight,
    overflows: overflows,
    safeZoneViolations: safeZoneViolations
  };
};
"""


def _wrap_document(
    *,
    width: int,
    height: int,
    extra_css: str,
    body_html: str,
    source_text: str,
    frame_js: str,
) -> str:
    pad_x = max(40, round(width * 0.05))
    pad_top = max(44, round(height * 0.065))
    pad_bottom = max(118, round(height * 0.135))
    src_bottom = max(32, round(height * 0.045))
    src_max_w = max(260, round(width * YT_SAFE_X_FRAC) - pad_x - 24)

    css = (
        _BASE_CSS.replace("__W__", str(width))
        .replace("__H__", str(height))
        .replace("__PAD_X__", str(pad_x))
        .replace("__PAD_TOP__", str(pad_top))
        .replace("__PAD_BOTTOM__", str(pad_bottom))
        .replace("__SRC_BOTTOM__", str(src_bottom))
        .replace("__SRC_MAX_W__", str(src_max_w))
    )
    js_common = _COMMON_JS.replace("__SAFE_X__", str(YT_SAFE_X_FRAC)).replace(
        "__SAFE_Y__", str(YT_SAFE_Y_FRAC)
    )
    source_html = ""
    if source_text:
        esc_src = html.escape(source_text)
        source_html = (
            f'<div class="source-badge" id="source-badge" data-safe-item data-check-overflow>'
            f'<span class="source-dot"></span><span>SOURCE // {esc_src}</span></div>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>{css}\n{extra_css}</style>
</head>
<body>
<div id="stage">
  <div id="grid-overlay"></div>
  <div id="content-safe" data-safe-item>
    {body_html}
  </div>
  {source_html}
</div>
<script>
{js_common}
window.__setFrame = function(progress, elapsed, duration) {{
  const srcBadge = document.getElementById('source-badge');
  if (srcBadge) {{
    const sp = easeOutCubic(win(elapsed, 0.25, 0.45));
    srcBadge.style.opacity = sp.toFixed(3);
    srcBadge.style.transform = `translateY(${{((1 - sp) * 12).toFixed(1)}}px)`;
  }}
  {frame_js}
}};
window.__setFrame(1.0, 4.0, 4.0);
window.__fitAllText();
</script>
</body>
</html>"""


def _accent_vars(accent: str) -> tuple[str, str, str]:
    mapping = {
        "emerald": ("var(--emerald)", "var(--emerald-bg)", "var(--emerald-bd)"),
        "crimson": ("var(--crimson)", "var(--crimson-bg)", "var(--crimson-bd)"),
        "amber": ("var(--amber)", "var(--amber-bg)", "var(--amber-bd)"),
        "sky": ("var(--sky)", "var(--sky-bg)", "var(--sky-bd)"),
    }
    return mapping.get(accent, mapping["sky"])


def _parse_stat_parts(value: str) -> tuple[str, float | None, int, str]:
    """Extract (prefix, numeric_target, decimals, suffix) for smooth count-up animation."""
    m = re.match(r"^([^0-9+-]*[+-]?)(\d+(?:,\d{3})*(?:\.\d+)?)(.*)$", value.strip())
    if not m:
        return ("", None, 0, value)
    prefix, num_s, suffix = m.groups()
    clean = num_s.replace(",", "")
    try:
        target = float(clean)
    except ValueError:
        return ("", None, 0, value)
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    return (prefix, target, decimals, suffix)


def build_template_html(
    template_id: str,
    props: dict[str, Any] | None,
    *,
    width: int = 1920,
    height: int = 1080,
    max_chars: int = 140,
) -> str:
    """Build deterministic self-contained HTML for ``template_id``."""
    p = validate_template_props(template_id, props, max_chars=max_chars)

    if template_id == "big_stat_card":
        fg_col, bg_col, bd_col = _accent_vars(p["accent"])
        prefix, num_target, decimals, suffix = _parse_stat_parts(p["value"])
        trend_html = ""
        if p["trend"]:
            arrow = "▼" if p["accent"] == "crimson" else ("▲" if p["accent"] == "emerald" else "•")
            trend_html = (
                f'<div id="stat-trend" class="trend-pill" data-check-overflow>'
                f"{arrow} {html.escape(p['trend'])}</div>"
            )
        sub_html = (
            f'<div id="stat-sub" class="stat-sub" data-fit data-min-px="18" data-check-overflow>'
            f"{html.escape(p['sublabel'])}</div>"
            if p["sublabel"]
            else ""
        )
        css = f"""
        .stat-card {{
          width: 100%;
          max-height: 100%;
          padding: 56px 72px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          gap: 24px;
          border-top: 4px solid {fg_col};
          position: relative;
        }}
        .stat-top {{ display: flex; align-items: center; justify-content: space-between; gap: 20px; }}
        .trend-pill {{
          padding: 8px 20px;
          border-radius: 999px;
          background: {bg_col};
          border: 1px solid {bd_col};
          color: {fg_col};
          font-family: var(--font-mono);
          font-size: 20px;
          font-weight: 700;
          white-space: nowrap;
        }}
        .stat-hero {{
          font-family: var(--font-mono);
          font-size: 148px;
          font-weight: 800;
          line-height: 1.15;
          letter-spacing: -0.03em;
          color: {fg_col};
          max-height: 230px;
          flex-shrink: 0;
          overflow: hidden;
        }}
        .stat-label {{
          font-size: 46px;
          font-weight: 700;
          line-height: 1.24;
          color: var(--fg-primary);
          max-height: 132px;
          flex-shrink: 0;
          overflow: hidden;
        }}
        .stat-sub {{
          font-size: 26px;
          line-height: 1.35;
          color: var(--fg-muted);
          max-height: 76px;
          overflow: hidden;
        }}
        """
        body = f"""
        <div id="stat-shell" class="card-shell stat-card" data-check-overflow>
          <div class="stat-top">
            <div class="eyebrow" style="color:{fg_col}">{html.escape(p['eyebrow'])}</div>
            {trend_html}
          </div>
          <div id="stat-val" class="stat-hero" data-fit data-min-px="64" data-check-overflow>{html.escape(p['value'])}</div>
          <div id="stat-lbl" class="stat-label" data-fit data-min-px="24" data-check-overflow>{html.escape(p['label'])}</div>
          {sub_html}
        </div>
        """
        has_num = "true" if num_target is not None else "false"
        target_js = f"{num_target or 0.0}"
        js = f"""
        const c = easeOutCubic(win(elapsed, 0.0, 0.42));
        const shell = document.getElementById('stat-shell');
        shell.style.opacity = c.toFixed(3);
        shell.style.transform = `translateY(${{((1 - c) * 28).toFixed(1)}}px) scale(${{(0.96 + 0.04 * c).toFixed(3)}})`;
        const valEl = document.getElementById('stat-val');
        const countP = easeOutCubic(win(elapsed, 0.05, Math.min(1.05, duration * 0.45)));
        if ({has_num}) {{
          const curVal = ({target_js} * countP).toFixed({decimals});
          valEl.textContent = {json.dumps(prefix)} + curVal + {json.dumps(suffix)};
        }}
        const lp = easeOutCubic(win(elapsed, 0.22, 0.45));
        const lbl = document.getElementById('stat-lbl');
        lbl.style.opacity = lp.toFixed(3);
        lbl.style.transform = `translateY(${{((1 - lp) * 16).toFixed(1)}}px)`;
        const tr = document.getElementById('stat-trend');
        if (tr) {{
          const tp = easeOutBack(win(elapsed, 0.35, 0.40));
          tr.style.opacity = clamp01(win(elapsed, 0.35, 0.25)).toFixed(3);
          tr.style.transform = `scale(${{(0.85 + 0.15 * tp).toFixed(3)}})`;
        }}
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    if template_id == "evidence_card":
        excerpt = p["excerpt"]
        hl = p["highlight_phrase"]
        idx = excerpt.lower().find(hl.lower())
        if idx >= 0 and hl:
            before = html.escape(excerpt[:idx])
            mid = html.escape(excerpt[idx : idx + len(hl)])
            after = html.escape(excerpt[idx + len(hl) :])
            excerpt_html = f'{before}<span id="ev-hl" class="ev-hl">{mid}</span>{after}'
        else:
            excerpt_html = f'<span id="ev-hl" class="ev-hl">{html.escape(excerpt)}</span>'

        css = """
        .ev-card {
          width: 100%;
          max-height: 100%;
          padding: 52px 64px;
          display: flex;
          flex-direction: column;
          gap: 28px;
          border-left: 6px solid var(--amber);
        }
        .ev-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 24px;
          padding-bottom: 20px;
          border-bottom: 1px solid var(--border-subtle);
        }
        .ev-pub {
          font-size: 28px;
          font-weight: 800;
          letter-spacing: 0.04em;
          color: var(--fg-primary);
          text-transform: uppercase;
        }
        .ev-date {
          font-family: var(--font-mono);
          font-size: 18px;
          padding: 6px 16px;
          border-radius: 8px;
          background: var(--amber-bg);
          border: 1px solid var(--amber-bd);
          color: var(--amber);
          white-space: nowrap;
        }
        .ev-quote {
          font-size: 42px;
          font-weight: 600;
          line-height: 1.42;
          color: var(--fg-primary);
          max-height: 360px;
          overflow: hidden;
        }
        .ev-hl {
          padding: 2px 8px;
          border-radius: 6px;
          background-image: linear-gradient(90deg, rgba(245, 158, 11, 0.34), rgba(16, 185, 129, 0.28));
          background-repeat: no-repeat;
          background-size: 100% 100%;
          color: #FFFFFF;
        }
        """
        body = f"""
        <div id="ev-shell" class="card-shell ev-card" data-check-overflow>
          <div class="ev-header">
            <div>
              <div class="eyebrow" style="color:var(--amber);margin-bottom:6px">{html.escape(p['tag'])}</div>
              <div class="ev-pub" data-fit data-min-px="18" data-check-overflow>{html.escape(p['publication'])}</div>
            </div>
            <div class="ev-date">{html.escape(p['date_header'])}</div>
          </div>
          <div id="ev-body" class="ev-quote" data-fit data-min-px="22" data-check-overflow>&ldquo;{excerpt_html}&rdquo;</div>
        </div>
        """
        js = """
        const c = easeOutCubic(win(elapsed, 0.0, 0.42));
        const shell = document.getElementById('ev-shell');
        shell.style.opacity = c.toFixed(3);
        shell.style.transform = `translateY(${((1 - c) * 24).toFixed(1)}px)`;
        const sweep = easeOutCubic(win(elapsed, 0.38, Math.min(1.1, duration * 0.45)));
        const hl = document.getElementById('ev-hl');
        if (hl) {
          hl.style.backgroundSize = `${(sweep * 100).toFixed(1)}% 100%`;
        }
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    if template_id == "split_comparison":
        pol_map = {
            "positive": ("var(--emerald)", "&#10003;", "var(--bg-panel-elev)"),
            "negative": ("var(--crimson)", "&#10005;", "var(--bg-panel)"),
            "neutral": ("var(--sky)", "&#9670;", "var(--bg-panel)"),
        }
        l_col, l_icon, l_bg = pol_map.get(p["left_polarity"], pol_map["negative"])
        r_col, r_icon, r_bg = pol_map.get(p["right_polarity"], pol_map["positive"])
        left_rows = "".join(
            f'<div class="cmp-item cmp-item-l" data-idx="{i}" data-fit data-min-px="17" data-check-overflow>'
            f'<span class="cmp-icon cmp-icon-l">{l_icon}</span><span>{html.escape(item)}</span></div>'
            for i, item in enumerate(p["left_items"])
        )
        right_rows = "".join(
            f'<div class="cmp-item cmp-item-r" data-idx="{i}" data-fit data-min-px="17" data-check-overflow>'
            f'<span class="cmp-icon cmp-icon-r">{r_icon}</span><span>{html.escape(item)}</span></div>'
            for i, item in enumerate(p["right_items"])
        )
        css = f"""
        .cmp-wrap {{ display: flex; flex-direction: column; gap: 22px; width: 100%; height: 100%; justify-content: center; }}
        .cmp-top-title {{ font-size: 36px; font-weight: 800; letter-spacing: -0.01em; color: var(--fg-primary); max-height: 52px; overflow: hidden; }}
        .cmp-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 32px; width: 100%; max-height: 680px; }}
        .cmp-col {{ padding: 34px 38px; display: flex; flex-direction: column; gap: 18px; }}
        .cmp-col-l {{ border-top: 4px solid {l_col}; background: {l_bg}; }}
        .cmp-col-r {{ border-top: 4px solid {r_col}; background: {r_bg}; }}
        .cmp-hdr {{ font-family: var(--font-mono); font-size: 24px; font-weight: 800; letter-spacing: 0.05em; text-transform: uppercase; padding-bottom: 12px; border-bottom: 1px solid var(--border-subtle); max-height: 48px; overflow: hidden; }}
        .cmp-hdr-l {{ color: {l_col}; }}
        .cmp-hdr-r {{ color: {r_col}; }}
        .cmp-item {{
          display: flex; align-items: flex-start; gap: 14px;
          padding: 14px 16px; border-radius: 14px;
          background: rgba(15, 23, 42, 0.65);
          border: 1px solid rgba(148, 163, 184, 0.12);
          font-size: 25px; line-height: 1.32; color: var(--fg-primary);
          max-height: 112px; overflow: hidden;
        }}
        .cmp-icon {{ font-family: var(--font-mono); font-weight: 800; font-size: 22px; flex-shrink: 0; margin-top: 2px; }}
        .cmp-icon-l {{ color: {l_col}; }}
        .cmp-icon-r {{ color: {r_col}; }}
        """
        body = f"""
        <div class="cmp-wrap" data-check-overflow>
          <div id="cmp-title" class="cmp-top-title" data-fit data-min-px="22" data-check-overflow>{html.escape(p['title'])}</div>
          <div class="cmp-grid">
            <div id="cmp-left" class="card-shell cmp-col cmp-col-l" data-check-overflow>
              <div class="cmp-hdr cmp-hdr-l" data-fit data-min-px="16" data-check-overflow>{html.escape(p['left_header'])}</div>
              {left_rows}
            </div>
            <div id="cmp-right" class="card-shell cmp-col cmp-col-r" data-check-overflow>
              <div class="cmp-hdr cmp-hdr-r" data-fit data-min-px="16" data-check-overflow>{html.escape(p['right_header'])}</div>
              {right_rows}
            </div>
          </div>
        </div>
        """
        js = """
        const headP = easeOutCubic(win(elapsed, 0.0, 0.35));
        document.getElementById('cmp-title').style.opacity = headP.toFixed(3);
        document.getElementById('cmp-left').style.opacity = headP.toFixed(3);
        document.getElementById('cmp-right').style.opacity = headP.toFixed(3);
        const allItems = document.querySelectorAll('.cmp-item');
        allItems.forEach((el) => {
          const idx = parseInt(el.getAttribute('data-idx') || '0', 10);
          const isRight = el.classList.contains('cmp-item-r');
          const delay = 0.25 + idx * 0.22 + (isRight ? 0.10 : 0.0);
          const ip = easeOutCubic(win(elapsed, delay, 0.36));
          el.style.opacity = ip.toFixed(3);
          el.style.transform = `translateY(${((1 - ip) * 16).toFixed(1)}px)`;
        });
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    if template_id == "rubric_checklist":
        if p["variant"] == "discussion_prompt":
            pills = "".join(
                f'<div class="disc-pill">{html.escape(opt)}</div>' for opt in p["options"]
            )
            css = """
            .disc-card {
              width: 100%; padding: 54px 68px; display: flex; flex-direction: column; gap: 28px;
              border-top: 4px solid var(--amber);
            }
            .disc-claim { font-size: 36px; line-height: 1.35; color: var(--fg-secondary); font-weight: 600; max-height: 160px; overflow: hidden; }
            .disc-q {
              font-size: 62px; font-weight: 800; line-height: 1.15; color: var(--fg-primary);
              padding: 20px 28px; border-radius: 18px;
              background: linear-gradient(90deg, rgba(245, 158, 11, 0.18), rgba(56, 189, 248, 0.12));
              border: 1px solid var(--amber-bd);
              max-height: 170px; overflow: hidden;
            }
            .disc-row { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
            .disc-pill {
              padding: 12px 26px; border-radius: 999px; font-family: var(--font-mono);
              font-size: 22px; font-weight: 700; color: var(--amber);
              background: var(--amber-bg); border: 1px solid var(--amber-bd);
            }
            """
            body = f"""
            <div id="disc-shell" class="card-shell disc-card" data-check-overflow>
              <div class="eyebrow" style="color:var(--amber)">{html.escape(p['eyebrow'])}</div>
              <div id="disc-claim" class="disc-claim" data-fit data-min-px="22" data-check-overflow>{html.escape(p['claim'])}</div>
              <div id="disc-q" class="disc-q" data-fit data-min-px="30" data-check-overflow>{html.escape(p['question'])}</div>
              <div id="disc-pills" class="disc-row">{pills}</div>
            </div>
            """
            js = """
            const c = easeOutCubic(win(elapsed, 0.0, 0.38));
            const sh = document.getElementById('disc-shell');
            sh.style.opacity = c.toFixed(3);
            sh.style.transform = `translateY(${((1 - c) * 22).toFixed(1)}px)`;
            const qp = easeOutBack(win(elapsed, 0.28, 0.45));
            const qEl = document.getElementById('disc-q');
            qEl.style.opacity = clamp01(win(elapsed, 0.28, 0.28)).toFixed(3);
            qEl.style.transform = `scale(${(0.94 + 0.06 * qp).toFixed(3)})`;
            const pp = easeOutCubic(win(elapsed, 0.55, 0.38));
            document.getElementById('disc-pills').style.opacity = pp.toFixed(3);
            """
            return _wrap_document(
                width=width,
                height=height,
                extra_css=css,
                body_html=body,
                source_text=p["source"],
                frame_js=js,
            )

        rows_html = []
        for idx, item in enumerate(p["items"]):
            fg_c, bg_c, bd_c = _accent_vars(item["tone"])
            det_html = (
                f'<div class="rub-detail" data-fit data-min-px="15" data-check-overflow>'
                f"{html.escape(item['detail'])}</div>"
                if item["detail"]
                else ""
            )
            rows_html.append(
                f'<div class="rub-row" data-idx="{idx}" data-check-overflow>'
                f'<div class="rub-text">'
                f'<div class="rub-lbl" data-fit data-min-px="20" data-check-overflow>{html.escape(item["label"])}</div>'
                f"{det_html}"
                f"</div>"
                f'<div class="rub-badge" style="color:{fg_c};background:{bg_c};border-color:{bd_c}">'
                f'{html.escape(item["status"])}</div>'
                f"</div>"
            )
        css = """
        .rub-wrap { width: 100%; display: flex; flex-direction: column; gap: 22px; }
        .rub-title { font-size: 38px; font-weight: 800; color: var(--fg-primary); max-height: 56px; overflow: hidden; }
        .rub-list { display: flex; flex-direction: column; gap: 16px; }
        .rub-row {
          padding: 24px 34px; border-radius: 18px;
          background: var(--bg-panel); border: 1px solid var(--border-subtle);
          display: flex; align-items: center; justify-content: space-between; gap: 28px;
          max-height: 138px; overflow: hidden;
        }
        .rub-text { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
        .rub-lbl { font-size: 29px; font-weight: 700; color: var(--fg-primary); max-height: 44px; overflow: hidden; }
        .rub-detail { font-size: 21px; color: var(--fg-muted); max-height: 36px; overflow: hidden; }
        .rub-badge {
          padding: 10px 24px; border-radius: 999px; border: 1px solid;
          font-family: var(--font-mono); font-size: 21px; font-weight: 800;
          letter-spacing: 0.06em; white-space: nowrap; flex-shrink: 0;
        }
        """
        body = f"""
        <div class="rub-wrap" data-check-overflow>
          <div>
            <div class="eyebrow" style="margin-bottom:6px">{html.escape(p['eyebrow'])}</div>
            <div id="rub-title" class="rub-title" data-fit data-min-px="22" data-check-overflow>{html.escape(p['title'])}</div>
          </div>
          <div class="rub-list">{''.join(rows_html)}</div>
        </div>
        """
        js = """
        document.getElementById('rub-title').style.opacity = easeOutCubic(win(elapsed, 0.0, 0.30)).toFixed(3);
        document.querySelectorAll('.rub-row').forEach((row) => {
          const idx = parseInt(row.getAttribute('data-idx') || '0', 10);
          const delay = 0.16 + idx * 0.24;
          const rp = easeOutCubic(win(elapsed, delay, 0.38));
          row.style.opacity = rp.toFixed(3);
          row.style.transform = `translateX(${((1 - rp) * -26).toFixed(1)}px)`;
          const badge = row.querySelector('.rub-badge');
          const bp = easeOutBack(win(elapsed, delay + 0.14, 0.34));
          badge.style.opacity = clamp01(win(elapsed, delay + 0.14, 0.20)).toFixed(3);
          badge.style.transform = `scale(${(0.82 + 0.18 * bp).toFixed(3)})`;
        });
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    if template_id == "animated_chart":
        x_labels = p["x_labels"]
        series = p["series"]
        all_vals = [v for s in series for v in s["values"]] or [0.0, 1.0]
        min_v = min(0.0, min(all_vals))
        max_v = max(all_vals)
        if abs(max_v - min_v) < 1e-6:
            max_v = min_v + 1.0
        # SVG viewport 1460 x 540
        svg_w, svg_h = 1460, 520
        pl, pr, pt, pb = 110, 60, 36, 72
        cw, ch = svg_w - pl - pr, svg_h - pt - pb

        grid_svg: list[str] = []
        for g in range(5):
            frac = g / 4.0
            gy = pt + ch * (1.0 - frac)
            gv = min_v + (max_v - min_v) * frac
            lbl_num = f"{gv:.0f}" if abs(gv) >= 10 or gv == int(gv) else f"{gv:.1f}"
            grid_svg.append(
                f'<line x1="{pl}" y1="{gy:.1f}" x2="{svg_w - pr}" y2="{gy:.1f}" '
                f'stroke="rgba(148,163,184,0.16)" stroke-width="1.5" />'
                f'<text x="{pl - 16}" y="{gy + 6:.1f}" text-anchor="end" fill="#94A3B8" '
                f'font-family="Consolas, monospace" font-size="18">{html.escape(lbl_num + p["y_unit"])}</text>'
            )

        colors = ["#38BDF8", "#10B981"]
        shapes_svg: list[str] = []
        n_x = len(x_labels)

        if p["chart_type"] == "line":
            step_x = cw / max(n_x - 1, 1)
            for i, xl in enumerate(x_labels):
                xx = pl + i * step_x
                grid_svg.append(
                    f'<text x="{xx:.1f}" y="{svg_h - 18}" text-anchor="middle" fill="#CBD5E1" '
                    f'font-family="Segoe UI, sans-serif" font-weight="600" font-size="20">{html.escape(xl)}</text>'
                )
            for s_idx, s in enumerate(series):
                col = colors[s_idx % len(colors)]
                pts: list[tuple[float, float]] = []
                for i, v in enumerate(s["values"][:n_x]):
                    xx = pl + i * step_x
                    yy = pt + ch * (1.0 - (v - min_v) / (max_v - min_v))
                    pts.append((xx, yy))
                d_str = " ".join(
                    f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts)
                )
                shapes_svg.append(
                    f'<path class="chart-line" data-s="{s_idx}" d="{d_str}" fill="none" '
                    f'stroke="{col}" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" '
                    f'pathLength="1000" stroke-dasharray="1000" stroke-dashoffset="0" />'
                )
                for i, (x, y) in enumerate(pts):
                    shapes_svg.append(
                        f'<circle class="chart-dot" data-i="{i}" data-n="{n_x}" cx="{x:.1f}" cy="{y:.1f}" '
                        f'r="8" fill="#090D16" stroke="{col}" stroke-width="4" />'
                    )
        else:
            vals = series[0]["values"][:n_x]
            slot_w = cw / max(n_x, 1)
            bar_w = min(130.0, slot_w * 0.58)
            zero_y = pt + ch * (1.0 - (0.0 - min_v) / (max_v - min_v))
            for i, (xl, v) in enumerate(zip(x_labels, vals, strict=False)):
                cx = pl + slot_w * (i + 0.5)
                vy = pt + ch * (1.0 - (v - min_v) / (max_v - min_v))
                bh = max(4.0, abs(zero_y - vy))
                by = min(zero_y, vy)
                is_hl = i == p["highlight_index"] or (p["highlight_index"] < 0 and i == n_x - 1)
                col = "#10B981" if is_hl else "#38BDF8"
                v_str = f"{v:.0f}" if abs(v) >= 10 or v == int(v) else f"{v:.1f}"
                shapes_svg.append(
                    f'<rect class="chart-bar" data-i="{i}" data-y="{by:.1f}" data-h="{bh:.1f}" '
                    f'data-zy="{zero_y:.1f}" x="{cx - bar_w / 2:.1f}" y="{by:.1f}" '
                    f'width="{bar_w:.1f}" height="{bh:.1f}" rx="8" fill="{col}" />'
                    f'<text class="chart-val" data-i="{i}" x="{cx:.1f}" y="{max(22.0, by - 12):.1f}" '
                    f'text-anchor="middle" fill="{col}" font-family="Consolas, monospace" '
                    f'font-weight="700" font-size="20">{html.escape(v_str + p["y_unit"])}</text>'
                    f'<text x="{cx:.1f}" y="{svg_h - 18}" text-anchor="middle" fill="#CBD5E1" '
                    f'font-family="Segoe UI, sans-serif" font-weight="600" font-size="20">{html.escape(xl)}</text>'
                )

        legend_html = ""
        if len(series) > 1:
            leg_items = "".join(
                f'<span class="leg-item"><span class="leg-swatch" style="background:{colors[i % 2]}"></span>'
                f'{html.escape(s["name"])}</span>'
                for i, s in enumerate(series)
            )
            legend_html = f'<div class="leg-row">{leg_items}</div>'

        callout_html = ""
        if p["callout_value"] or p["callout_label"]:
            callout_html = (
                f'<div id="chart-callout" class="chart-callout">'
                f'<span class="callout-lbl">{html.escape(p["callout_label"])}</span>'
                f'<span class="callout-val">{html.escape(p["callout_value"])}</span></div>'
            )

        css = """
        .chart-card { width: 100%; padding: 40px 52px; display: flex; flex-direction: column; gap: 18px; }
        .chart-hdr { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
        .chart-title { font-size: 36px; font-weight: 800; color: var(--fg-primary); max-height: 50px; overflow: hidden; }
        .leg-row { display: flex; align-items: center; gap: 22px; font-family: var(--font-mono); font-size: 18px; color: var(--fg-secondary); }
        .leg-item { display: inline-flex; align-items: center; gap: 8px; }
        .leg-swatch { width: 14px; height: 14px; border-radius: 4px; display: inline-block; }
        .chart-callout {
          padding: 10px 22px; border-radius: 14px; background: var(--emerald-bg);
          border: 1px solid var(--emerald-bd); display: inline-flex; align-items: center; gap: 12px;
          font-family: var(--font-mono); white-space: nowrap;
        }
        .callout-lbl { font-size: 17px; color: var(--fg-secondary); }
        .callout-val { font-size: 24px; font-weight: 800; color: var(--emerald); }
        .chart-svg-wrap { width: 100%; display: flex; justify-content: center; }
        svg.chart-svg { width: 100%; height: auto; max-height: 520px; overflow: visible; }
        """
        body = f"""
        <div id="chart-shell" class="card-shell chart-card" data-check-overflow>
          <div class="chart-hdr">
            <div>
              <div class="eyebrow" style="margin-bottom:4px">DATA TELEMETRY</div>
              <div class="chart-title" data-fit data-min-px="20" data-check-overflow>{html.escape(p['title'])}</div>
            </div>
            <div style="display:flex;align-items:center;gap:20px">{legend_html}{callout_html}</div>
          </div>
          <div class="chart-svg-wrap">
            <svg class="chart-svg" viewBox="0 0 {svg_w} {svg_h}">
              {''.join(grid_svg)}
              {''.join(shapes_svg)}
            </svg>
          </div>
        </div>
        """
        js = """
        const c = easeOutCubic(win(elapsed, 0.0, 0.35));
        document.getElementById('chart-shell').style.opacity = c.toFixed(3);
        const drawDur = Math.min(1.35, Math.max(0.65, duration * 0.50));
        document.querySelectorAll('.chart-line').forEach((path) => {
          const sIdx = parseInt(path.getAttribute('data-s') || '0', 10);
          const lp = easeOutCubic(win(elapsed, 0.15 + sIdx * 0.18, drawDur));
          path.style.strokeDashoffset = ((1 - lp) * 1000).toFixed(1);
        });
        document.querySelectorAll('.chart-dot').forEach((dot) => {
          const idx = parseInt(dot.getAttribute('data-i') || '0', 10);
          const total = Math.max(1, parseInt(dot.getAttribute('data-n') || '1', 10) - 1);
          const dp = easeOutBack(win(elapsed, 0.18 + (idx / total) * drawDur * 0.85, 0.25));
          dot.style.opacity = clamp01(dp).toFixed(3);
        });
        document.querySelectorAll('.chart-bar').forEach((bar) => {
          const idx = parseInt(bar.getAttribute('data-i') || '0', 10);
          const targetY = parseFloat(bar.getAttribute('data-y') || '0');
          const targetH = parseFloat(bar.getAttribute('data-h') || '0');
          const zeroY = parseFloat(bar.getAttribute('data-zy') || '0');
          const bp = easeOutCubic(win(elapsed, 0.14 + idx * 0.12, 0.52));
          const curH = Math.max(2, targetH * bp);
          const curY = targetY < zeroY ? (zeroY - curH) : zeroY;
          bar.setAttribute('y', curY.toFixed(1));
          bar.setAttribute('height', curH.toFixed(1));
        });
        document.querySelectorAll('.chart-val').forEach((lbl) => {
          const idx = parseInt(lbl.getAttribute('data-i') || '0', 10);
          lbl.style.opacity = easeOutCubic(win(elapsed, 0.38 + idx * 0.12, 0.30)).toFixed(3);
        });
        const co = document.getElementById('chart-callout');
        if (co) {
          const cp = easeOutBack(win(elapsed, 0.55, 0.38));
          co.style.opacity = clamp01(win(elapsed, 0.55, 0.22)).toFixed(3);
          co.style.transform = `scale(${(0.88 + 0.12 * cp).toFixed(3)})`;
        }
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    if template_id == "architecture_flow":
        nodes = p["nodes"]
        n_nodes = len(nodes)
        # Clean 1-row layout for 3-4 nodes; 2-row grid for 5-6 nodes so zero label overlap!
        two_row = n_nodes > 4
        if not two_row:
            items_html: list[str] = []
            for i, node in enumerate(nodes):
                det = (
                    f'<div class="arch-det" data-fit data-min-px="14" data-check-overflow>'
                    f'{html.escape(node["detail"])}</div>'
                    if node["detail"]
                    else ""
                )
                hot_cls = " arch-hot" if node["highlight"] else ""
                items_html.append(
                    f'<div class="arch-node{hot_cls}" data-step="{i}" data-check-overflow>'
                    f'<div class="arch-tag">{html.escape(node["tag"])}</div>'
                    f'<div class="arch-name" data-fit data-min-px="18" data-check-overflow>{html.escape(node["name"])}</div>'
                    f"{det}</div>"
                )
                if i < n_nodes - 1:
                    items_html.append(
                        f'<div class="arch-arrow" data-step="{i}"><span>&#10140;</span></div>'
                    )
            flow_body = f'<div class="arch-row-single">{"".join(items_html)}</div>'
        else:
            top_n = math.ceil(n_nodes / 2)
            row1_nodes = nodes[:top_n]
            row2_nodes = nodes[top_n:]

            def _render_row(r_nodes: list[dict[str, Any]], offset: int) -> str:
                parts: list[str] = []
                for j, nd in enumerate(r_nodes):
                    step = offset + j
                    det = (
                        f'<div class="arch-det" data-fit data-min-px="14" data-check-overflow>'
                        f'{html.escape(nd["detail"])}</div>'
                        if nd["detail"]
                        else ""
                    )
                    hot_cls = " arch-hot" if nd["highlight"] else ""
                    parts.append(
                        f'<div class="arch-node{hot_cls}" data-step="{step}" data-check-overflow>'
                        f'<div class="arch-tag">{html.escape(nd["tag"])}</div>'
                        f'<div class="arch-name" data-fit data-min-px="18" data-check-overflow>{html.escape(nd["name"])}</div>'
                        f"{det}</div>"
                    )
                    if j < len(r_nodes) - 1:
                        parts.append(
                            f'<div class="arch-arrow" data-step="{step}"><span>&#10140;</span></div>'
                        )
                return "".join(parts)

            flow_body = (
                f'<div class="arch-two-rows">'
                f'<div class="arch-row-single">{_render_row(row1_nodes, 0)}</div>'
                f'<div class="arch-bridge" data-step="{top_n - 1}">&#8628; PIPELINE STAGE HANDOFF &#8628;</div>'
                f'<div class="arch-row-single">{_render_row(row2_nodes, top_n)}</div>'
                f"</div>"
            )

        sub_html = (
            f'<div class="arch-sub" data-fit data-min-px="16" data-check-overflow>{html.escape(p["subtitle"])}</div>'
            if p["subtitle"]
            else ""
        )
        css = """
        .arch-wrap { width: 100%; display: flex; flex-direction: column; gap: 28px; justify-content: center; }
        .arch-title { font-size: 38px; font-weight: 800; color: var(--fg-primary); max-height: 54px; overflow: hidden; }
        .arch-sub { font-size: 22px; color: var(--fg-muted); margin-top: 6px; max-height: 36px; overflow: hidden; }
        .arch-two-rows { display: flex; flex-direction: column; gap: 18px; width: 100%; }
        .arch-row-single { display: flex; align-items: stretch; justify-content: center; gap: 14px; width: 100%; }
        .arch-bridge {
          text-align: center; font-family: var(--font-mono); font-size: 17px; font-weight: 700;
          color: var(--sky); letter-spacing: 0.1em; padding: 4px 0;
        }
        .arch-node {
          flex: 1; min-width: 0; padding: 26px 24px; border-radius: 20px;
          background: var(--bg-panel); border: 2px solid var(--border-subtle);
          display: flex; flex-direction: column; justify-content: center; gap: 10px;
          max-height: 230px; overflow: hidden;
        }
        .arch-tag { font-family: var(--font-mono); font-size: 15px; font-weight: 700; letter-spacing: 0.08em; color: var(--sky); text-transform: uppercase; }
        .arch-name { font-size: 27px; font-weight: 800; color: var(--fg-primary); line-height: 1.22; max-height: 70px; overflow: hidden; }
        .arch-det { font-size: 19px; color: var(--fg-muted); line-height: 1.3; max-height: 56px; overflow: hidden; }
        .arch-arrow {
          display: flex; align-items: center; justify-content: center;
          font-size: 32px; color: var(--sky); width: 38px; flex-shrink: 0;
        }
        """
        body = f"""
        <div id="arch-shell" class="arch-wrap" data-check-overflow>
          <div>
            <div class="eyebrow" style="margin-bottom:6px">SYSTEM TOPOLOGY</div>
            <div class="arch-title" data-fit data-min-px="22" data-check-overflow>{html.escape(p['title'])}</div>
            {sub_html}
          </div>
          {flow_body}
        </div>
        """
        js = f"""
        const totalSteps = {n_nodes};
        const stepSpan = Math.min(1.8, Math.max(0.8, duration * 0.62)) / Math.max(1, totalSteps);
        document.querySelectorAll('.arch-node').forEach((node) => {{
          const s = parseInt(node.getAttribute('data-step') || '0', 10);
          const np = easeOutCubic(win(elapsed, 0.10 + s * stepSpan, 0.36));
          const isHot = node.classList.contains('arch-hot');
          node.style.opacity = (0.25 + 0.75 * np).toFixed(3);
          node.style.transform = `translateY(${{((1 - np) * 14).toFixed(1)}}px)`;
          if (np > 0.65) {{
            node.style.borderColor = isHot ? 'rgba(16, 185, 129, 0.75)' : 'rgba(56, 189, 248, 0.58)';
            node.style.boxShadow = isHot
              ? '0 0 32px rgba(16, 185, 129, 0.22)'
              : '0 0 24px rgba(56, 189, 248, 0.14)';
          }} else {{
            node.style.borderColor = 'rgba(148, 163, 184, 0.16)';
            node.style.boxShadow = 'none';
          }}
        }});
        document.querySelectorAll('.arch-arrow, .arch-bridge').forEach((arr) => {{
          const s = parseInt(arr.getAttribute('data-step') || '0', 10);
          const ap = easeOutCubic(win(elapsed, 0.22 + s * stepSpan, 0.28));
          arr.style.opacity = (0.18 + 0.82 * ap).toFixed(3);
        }});
        """
        return _wrap_document(
            width=width,
            height=height,
            extra_css=css,
            body_html=body,
            source_text=p["source"],
            frame_js=js,
        )

    # code_ide_mockup
    hl_set = set(p["highlight_lines"])
    file_rows = "".join(
        f'<div class="ide-file{" ide-file-active" if f == p["filename"] else ""}" data-check-overflow>'
        f'{html.escape(f)}</div>'
        for f in p["files"]
    )
    code_rows = []
    for idx, line in enumerate(p["code_lines"], start=1):
        is_hl = idx in hl_set
        cls = "ide-line ide-line-hl" if is_hl else "ide-line ide-line-dim"
        code_rows.append(
            f'<div class="{cls}" data-line="{idx}" data-check-overflow>'
            f'<span class="ide-ln">{idx:02d}</span>'
            f'<span class="ide-code">{_highlight_code_line(line)}</span>'
            f"</div>"
        )
    side_html = ""
    if p["side_panel_items"]:
        s_items = "".join(
            f'<div class="ide-side-item" data-fit data-min-px="14" data-check-overflow>'
            f"{html.escape(it)}</div>"
            for it in p["side_panel_items"]
        )
        side_html = (
            f'<div class="ide-side" data-check-overflow>'
            f'<div class="ide-side-hdr">{html.escape(p["side_panel_title"])}</div>'
            f"{s_items}</div>"
        )
    st_col, st_bg, st_bd = _accent_vars(
        "crimson"
        if p["status_tone"] == "fail"
        else ("emerald" if p["status_tone"] == "pass" else "sky")
    )
    css = f"""
    .ide-shell {{
      width: 100%; max-height: 760px; display: flex; flex-direction: column;
      background: #0D1117; border: 1px solid rgba(148, 163, 184, 0.24); border-radius: 20px;
      box-shadow: 0 28px 70px rgba(0, 0, 0, 0.65); overflow: hidden;
    }}
    .ide-titlebar {{
      height: 52px; padding: 0 22px; background: #161B22;
      border-bottom: 1px solid rgba(148, 163, 184, 0.16);
      display: flex; align-items: center; justify-content: space-between;
    }}
    .ide-dots {{ display: flex; gap: 9px; }}
    .ide-dot {{ width: 13px; height: 13px; border-radius: 50%; }}
    .ide-tab {{
      font-family: var(--font-mono); font-size: 17px; font-weight: 600; color: var(--fg-primary);
      padding: 6px 16px; border-radius: 8px; background: #0D1117; border: 1px solid rgba(148,163,184,0.18);
    }}
    .ide-body {{ display: flex; flex: 1; min-height: 0; overflow: hidden; }}
    .ide-tree {{
      width: 240px; padding: 20px 14px; background: #090D14;
      border-right: 1px solid rgba(148, 163, 184, 0.14);
      display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;
    }}
    .ide-tree-hdr {{ font-family: var(--font-mono); font-size: 13px; color: var(--fg-muted); letter-spacing: 0.1em; margin-bottom: 6px; padding: 0 8px; }}
    .ide-file {{
      font-family: var(--font-mono); font-size: 16px; color: var(--fg-muted);
      padding: 8px 12px; border-radius: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .ide-file-active {{ background: rgba(56, 189, 248, 0.14); color: var(--sky); font-weight: 700; }}
    .ide-editor {{
      flex: 1; min-width: 0; padding: 22px 0; display: flex; flex-direction: column; gap: 4px;
      font-family: var(--font-mono); font-size: 21px; line-height: 1.48; overflow: hidden;
    }}
    .ide-line {{
      display: flex; align-items: center; padding: 3px 24px; border-left: 4px solid transparent;
      white-space: nowrap; overflow: hidden;
    }}
    .ide-ln {{ width: 44px; color: #484F58; user-select: none; flex-shrink: 0; font-size: 18px; }}
    .ide-code {{ overflow: hidden; text-overflow: ellipsis; }}
    .ide-side {{
      width: 340px; padding: 20px 22px; background: #0B1019;
      border-left: 1px solid rgba(148, 163, 184, 0.14);
      display: flex; flex-direction: column; gap: 14px; flex-shrink: 0;
    }}
    .ide-side-hdr {{ font-family: var(--font-mono); font-size: 14px; font-weight: 700; color: var(--sky); letter-spacing: 0.1em; }}
    .ide-side-item {{
      padding: 12px 14px; border-radius: 10px; background: rgba(22, 27, 34, 0.9);
      border: 1px solid rgba(148, 163, 184, 0.15); font-size: 17px; line-height: 1.35;
      color: var(--fg-secondary); max-height: 92px; overflow: hidden;
    }}
    .ide-statusbar {{
      height: 50px; padding: 0 24px; background: #161B22;
      border-top: 1px solid rgba(148, 163, 184, 0.16);
      display: flex; align-items: center; justify-content: space-between;
      font-family: var(--font-mono); font-size: 16px; color: var(--fg-muted);
    }}
    .ide-status-pill {{
      padding: 6px 16px; border-radius: 999px;
      color: {st_col}; background: {st_bg}; border: 1px solid {st_bd};
      font-weight: 700; font-size: 16px;
    }}
    """
    body = f"""
    <div id="ide-shell" class="ide-shell" data-check-overflow>
      <div class="ide-titlebar">
        <div class="ide-dots">
          <span class="ide-dot" style="background:#FF5F56"></span>
          <span class="ide-dot" style="background:#FFBD2E"></span>
          <span class="ide-dot" style="background:#27C93F"></span>
        </div>
        <div class="ide-tab">{html.escape(p['filename'])}</div>
        <div style="font-family:var(--font-mono);font-size:14px;color:var(--fg-muted)">{html.escape(p['language'].upper())}</div>
      </div>
      <div class="ide-body">
        <div class="ide-tree">
          <div class="ide-tree-hdr">WORKSPACE</div>
          {file_rows}
        </div>
        <div id="ide-editor" class="ide-editor" data-fit data-min-px="14" data-check-overflow>
          {''.join(code_rows)}
        </div>
        {side_html}
      </div>
      <div class="ide-statusbar">
        <span>UTF-8 // LF // {len(p['code_lines'])} LINES</span>
        <span id="ide-status" class="ide-status-pill">{html.escape(p['status_text'])}</span>
      </div>
    </div>
    """
    js = """
    const c = easeOutCubic(win(elapsed, 0.0, 0.36));
    const sh = document.getElementById('ide-shell');
    sh.style.opacity = c.toFixed(3);
    sh.style.transform = `translateY(${((1 - c) * 20).toFixed(1)}px)`;
    const focusP = easeOutCubic(win(elapsed, 0.42, 0.45));
    document.querySelectorAll('.ide-line-hl').forEach((el) => {
      el.style.background = `rgba(16, 185, 129, ${(0.18 * focusP).toFixed(3)})`;
      el.style.borderLeftColor = `rgba(16, 185, 129, ${(0.95 * focusP).toFixed(3)})`;
    });
    document.querySelectorAll('.ide-line-dim').forEach((el) => {
      el.style.opacity = (1.0 - 0.36 * focusP).toFixed(3);
    });
    const st = document.getElementById('ide-status');
    const sp = easeOutBack(win(elapsed, 0.62, 0.35));
    st.style.opacity = clamp01(win(elapsed, 0.62, 0.22)).toFixed(3);
    st.style.transform = `scale(${(0.88 + 0.12 * sp).toFixed(3)})`;
    """
    return _wrap_document(
        width=width,
        height=height,
        extra_css=css,
        body_html=body,
        source_text=p["source"],
        frame_js=js,
    )


# --------------------------------------------------------------------------- Playwright Session
class _PlaywrightSession:
    """Process-scoped headless Chromium session reused across template renders."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._pages: dict[tuple[int, int], Any] = {}

    def page(self, width: int, height: int) -> Any:
        key = (int(width), int(height))
        if key in self._pages:
            return self._pages[key]
        if self._pw is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(
            viewport={"width": key[0], "height": key[1]},
            device_scale_factor=1,
        )
        pg = ctx.new_page()
        self._pages[key] = pg
        return pg

    def close(self) -> None:
        for pg in list(self._pages.values()):
            with contextlib.suppress(Exception):
                pg.context.close()
        self._pages.clear()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                self._pw.stop()
            self._pw = None


_SESSION = _PlaywrightSession()
atexit.register(_SESSION.close)


def close_motion_browser() -> None:
    """Explicitly release any cached headless Chromium instance."""
    _SESSION.close()


@dataclass(frozen=True)
class LayoutAudit:
    template_id: str
    width: int
    height: int
    overflows: list[dict[str, Any]]
    safe_zone_violations: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return not self.overflows and not self.safe_zone_violations


class TemplateRegistry:
    """Registry and deterministic renderer for all 7 dark-mode motion-graphics templates."""

    TEMPLATE_IDS: tuple[str, ...] = MOTION_TEMPLATE_IDS

    def __init__(
        self,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        ffmpeg_path: str = "",
        video_encoder: str = "auto",
        settings: Any = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.ffmpeg_path = ffmpeg_path
        self.video_encoder = video_encoder
        self._settings = settings

    def max_chars_for(self, template_id: str) -> int:
        if self._settings is not None and hasattr(self._settings, "template_spec"):
            _, _, max_c = self._settings.template_spec(template_id)
            return int(max_c)
        defaults = {
            "big_stat_card": 90,
            "evidence_card": 220,
            "split_comparison": 140,
            "rubric_checklist": 110,
            "animated_chart": 90,
            "architecture_flow": 80,
            "code_ide_mockup": 380,
        }
        return defaults.get(template_id, 120)

    def inspect_layout(
        self,
        template_id: str,
        props: dict[str, Any] | None,
        *,
        duration_sec: float = 4.0,
        screenshot_path: str | Path | None = None,
        screenshot_progress: float = 0.65,
    ) -> LayoutAudit:
        """Load the template in headless Chromium, run auto-fit, and verify zero DOM overflow
        and zero overlap with the bottom-right YouTube timestamp safe zone."""
        doc = build_template_html(
            template_id,
            props,
            width=self.width,
            height=self.height,
            max_chars=self.max_chars_for(template_id),
        )
        pg = _SESSION.page(self.width, self.height)
        pg.set_content(doc, wait_until="load")
        pg.evaluate(
            "(d) => { window.__setFrame(1.0, d, d); window.__fitAllText(); }",
            float(duration_sec),
        )
        end_report = pg.evaluate("() => window.__inspectLayout()")

        mid_t = float(duration_sec) * max(0.0, min(1.0, float(screenshot_progress)))
        pg.evaluate(
            "([p, e, d]) => { window.__setFrame(p, e, d); }",
            [float(screenshot_progress), mid_t, float(duration_sec)],
        )
        mid_report = pg.evaluate("() => window.__inspectLayout()")
        if screenshot_path is not None:
            out_png = Path(screenshot_path)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(out_png), type="png")

        overflows = list(end_report.get("overflows") or []) + [
            x
            for x in (mid_report.get("overflows") or [])
            if x not in (end_report.get("overflows") or [])
        ]
        violations = list(end_report.get("safeZoneViolations") or []) + [
            x
            for x in (mid_report.get("safeZoneViolations") or [])
            if x not in (end_report.get("safeZoneViolations") or [])
        ]
        return LayoutAudit(
            template_id=template_id,
            width=self.width,
            height=self.height,
            overflows=overflows,
            safe_zone_violations=violations,
        )

    def render_template(
        self,
        template_id: str,
        props: dict[str, Any] | None,
        duration_sec: float,
        output_path: str | Path,
        *,
        mid_screenshot_path: str | Path | None = None,
    ) -> Path:
        """Render ``template_id`` into an exact-duration H.264 yuv420p ``.mp4`` clip."""
        exe = resolve_ffmpeg(self.ffmpeg_path)
        if exe is None:
            raise RenderError("ffmpeg executable not found; cannot encode motion-graphics clip.")

        dur = max(0.2, float(duration_sec))
        total_frames = max(2, int(round(dur * self.fps)))
        # After 2.2s every template's entrance/draw animation has settled cleanly; reuse the settled
        # PNG bytes for the hold tail so a 6s clip renders in <0.8s with zero visual difference.
        settle_sec = min(dur, 2.2)

        doc = build_template_html(
            template_id,
            props,
            width=self.width,
            height=self.height,
            max_chars=self.max_chars_for(template_id),
        )
        pg = _SESSION.page(self.width, self.height)
        pg.set_content(doc, wait_until="load")
        pg.evaluate(
            "(d) => { window.__setFrame(1.0, d, d); window.__fitAllText(); }",
            dur,
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        mid_frame_idx = total_frames // 2

        encoder = _select_encoder(exe, self.video_encoder)
        try:
            self._pipe_frames_to_ffmpeg(
                pg=pg,
                exe=exe,
                encoder=encoder,
                out=out,
                total_frames=total_frames,
                dur=dur,
                settle_sec=settle_sec,
                mid_frame_idx=mid_frame_idx,
                mid_screenshot_path=mid_screenshot_path,
            )
        except Exception:
            if encoder == "libx264":
                raise
            self._pipe_frames_to_ffmpeg(
                pg=pg,
                exe=exe,
                encoder="libx264",
                out=out,
                total_frames=total_frames,
                dur=dur,
                settle_sec=settle_sec,
                mid_frame_idx=mid_frame_idx,
                mid_screenshot_path=mid_screenshot_path,
            )
        return out

    def _pipe_frames_to_ffmpeg(
        self,
        *,
        pg: Any,
        exe: str,
        encoder: str,
        out: Path,
        total_frames: int,
        dur: float,
        settle_sec: float,
        mid_frame_idx: int,
        mid_screenshot_path: str | Path | None,
    ) -> None:
        opts = [item for k, v in _encoder_opts(encoder).items() for item in (f"-{k}", str(v))]
        cmd = [
            exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-vf",
            f"scale={self.width}:{self.height}:in_range=pc:out_range=tv,setsar=1,format=yuv420p",
            "-c:v",
            encoder,
            *opts,
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-r",
            str(self.fps),
            "-frames:v",
            str(total_frames),
            str(out),
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        settled_frame: bytes | None = None
        try:
            assert proc.stdin is not None
            for f_idx in range(total_frames):
                elapsed = f_idx / float(self.fps)
                progress = f_idx / float(max(1, total_frames - 1))
                if elapsed <= settle_sec or settled_frame is None:
                    pg.evaluate(
                        "([p, e, d]) => { window.__setFrame(p, e, d); }",
                        [progress, elapsed, dur],
                    )
                    frame_bytes = pg.screenshot(type="jpeg", quality=98)
                    if elapsed >= settle_sec:
                        settled_frame = frame_bytes
                    if mid_screenshot_path is not None and f_idx == mid_frame_idx:
                        sp = Path(mid_screenshot_path)
                        sp.parent.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(sp), type="png")
                else:
                    frame_bytes = settled_frame
                    if mid_screenshot_path is not None and f_idx == mid_frame_idx:
                        sp = Path(mid_screenshot_path)
                        sp.parent.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(sp), type="png")
                proc.stdin.write(frame_bytes)
            proc.stdin.close()
            proc.stdin = None
            stderr = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
            rc = proc.wait(timeout=60)
        finally:
            if proc.stdin is not None:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            if proc.poll() is None:
                proc.kill()
        if rc != 0 or not out.exists() or out.stat().st_size == 0:
            raise RenderError(
                f"Motion template {out.name} encode failed ({encoder}): {stderr[:300]}"
            )


def render_template(
    template_id: str,
    props: dict[str, Any] | None,
    duration_sec: float,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    ffmpeg_path: str = "",
    video_encoder: str = "auto",
    settings: Any = None,
    mid_screenshot_path: str | Path | None = None,
) -> Path:
    """Convenience entry point matching ``render_template(template_id, props, duration_sec, output_path)``."""
    if settings is not None and hasattr(settings, "resolution_wh"):
        width, height = settings.resolution_wh
        fps = getattr(settings, "video_fps", fps)
        ffmpeg_path = getattr(settings, "ffmpeg_path", ffmpeg_path)
        video_encoder = getattr(settings, "video_encoder", video_encoder)
    reg = TemplateRegistry(
        width=width,
        height=height,
        fps=fps,
        ffmpeg_path=ffmpeg_path,
        video_encoder=video_encoder,
        settings=settings,
    )
    return reg.render_template(
        template_id,
        props,
        duration_sec,
        output_path,
        mid_screenshot_path=mid_screenshot_path,
    )
