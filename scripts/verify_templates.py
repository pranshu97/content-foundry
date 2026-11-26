"""Render and verify all 7 programmatic motion-graphics templates (DOM layout + ffprobe parity).

Usage:
    python scripts/verify_templates.py [--out-dir output/template_verification]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from content_foundry.production.motion_graphics import (  # noqa: E402
    TemplateRegistry,
    close_motion_browser,
)

# Realistic, near-max-length payloads spanning all 7 templates + sub-variants.
VERIFICATION_CASES: tuple[tuple[str, str, dict[str, Any], float], ...] = (
    (
        "01_big_stat_card",
        "big_stat_card",
        {
            "eyebrow": "MARKET CONTRACTION SIGNAL",
            "value": "-18.4%",
            "label": "Entry-Level Software Postings Dropped Year-Over-Year",
            "sublabel": "Production ML and distributed infrastructure roles rose +14.2% in parallel",
            "trend": "YoY Hiring Shift",
            "source": "Tech Labor Market Benchmark (Q4)",
        },
        2.5,
    ),
    (
        "02_evidence_card",
        "evidence_card",
        {
            "publication": "COMPUTING RESEARCH ASSOCIATION // ANNUAL SURVEY",
            "date_header": "VERIFIED REPORT",
            "excerpt": (
                "Teams separating candidate retrieval from heavy cross-encoder ranking cut p99 "
                "serving tail latency by 41.6% without sacrificing top-10 precision."
            ),
            "highlight_phrase": "cut p99 serving tail latency by 41.6%",
            "source": "cra.org/research-benchmarks",
        },
        2.5,
    ),
    (
        "03_split_comparison",
        "split_comparison",
        {
            "title": "SYSTEM DESIGN INTERVIEW SIGNAL CONTRAST",
            "left_header": "NO-HIRE APPROACH",
            "right_header": "STRONG-HIRE EXECUTION",
            "left_items": [
                "Jumps straight to a giant model without bounding p99 latency or QPS",
                "Ignores training-serving feature skew and cache stampede failure modes",
                "Treats offline AUC gains as proof of production revenue lift",
            ],
            "right_items": [
                "Pins strict 15ms SLA budget and back-of-envelope memory math first",
                "Splits fast ANN candidate retrieval from bounded heavy re-ranking",
                "Defines online guardrails and graceful circuit-breaker degradation",
            ],
            "source": "Debrief Calibration Rubric",
        },
        2.5,
    ),
    (
        "04_rubric_checklist",
        "rubric_checklist",
        {
            "variant": "checklist",
            "title": "STAFF ENGINEERING DEBRIEF SCORECARD",
            "items": [
                {
                    "label": "Quantifies failure blast radius before picking storage",
                    "detail": "Explicitly bounds tail latency and retry storms",
                    "status": "STRONG HIRE",
                },
                {
                    "label": "Designs point-in-time correct feature hydration",
                    "detail": "Eliminates silent label leakage in offline replay",
                    "status": "HIRE",
                },
                {
                    "label": "Recites textbook architecture with zero trade-offs",
                    "detail": "Collapses under first throughput follow-up probe",
                    "status": "DOWNLEVEL",
                },
            ],
            "source": "Hiring Committee Bar Guide",
        },
        2.5,
    ),
    (
        "04b_rubric_discussion_prompt",
        "rubric_checklist",
        {
            "variant": "discussion_prompt",
            "claim": (
                "Unpaid 12-hour take-home coding assignments filter out employed senior engineers "
                "faster than weak candidates."
            ),
            "question": "Fair signal or broken filter?",
            "options": ["Fair Signal", "Broken Filter"],
            "source": "Engineering Hiring Debate",
        },
        2.5,
    ),
    (
        "05_animated_chart_line",
        "animated_chart",
        {
            "chart_type": "line",
            "title": "APPLICANT VOLUME VS ONSITE OFFER RATE",
            "x_labels": ["Y1", "Y2", "Y3", "Y4", "Y5"],
            "series": [
                {"name": "Applications / Role", "values": [22, 38, 61, 82, 94]},
                {"name": "Onsite Pass Rate", "values": [31, 27, 21, 16, 12]},
            ],
            "y_unit": "%",
            "callout_label": "Pass Rate",
            "callout_value": "12%",
            "source": "ATS Funnel Telemetry",
        },
        2.5,
    ),
    (
        "05b_animated_chart_bar",
        "animated_chart",
        {
            "chart_type": "bar",
            "title": "END-TO-END P99 LATENCY BUDGET BY STAGE",
            "items": [
                {"label": "API Gateway", "value": 1.8},
                {"label": "ANN Fetch", "value": 3.4},
                {"label": "Feature Store", "value": 2.9},
                {"label": "Cross-Ranker", "value": 14.2, "highlight": True},
            ],
            "y_unit": "ms",
            "callout_label": "Bottleneck",
            "callout_value": "14.2ms",
            "source": "Production Trace Span",
        },
        2.5,
    ),
    (
        "06_architecture_flow",
        "architecture_flow",
        {
            "title": "REAL-TIME TWO-STAGE RANKING ARCHITECTURE",
            "subtitle": "Sub-20ms serving path across 10M indexed items",
            "nodes": [
                {"tag": "STAGE 1", "name": "Edge Gateway", "detail": "Auth + rate limit"},
                {"tag": "STAGE 2", "name": "ANN Index", "detail": "10M -> 400 items"},
                {"tag": "STAGE 3", "name": "Feature Cache", "detail": "2ms batch read"},
                {"tag": "STAGE 4", "name": "Light Scorer", "detail": "400 -> 40 items"},
                {
                    "tag": "STAGE 5",
                    "name": "Cross-Encoder",
                    "detail": "Top 10 + SLA guard",
                    "highlight": True,
                },
            ],
            "source": "Serving System Blueprint",
        },
        2.5,
    ),
    (
        "07_code_ide_mockup",
        "code_ide_mockup",
        {
            "filename": "ranker_guard.py",
            "language": "python",
            "files": ["ranker_guard.py", "ann_client.py", "feature_hydrator.py", "test_sla.py"],
            "code_lines": [
                "async def score_with_sla_guard(req: Request, budget_ms: float = 18.0):",
                "    candidates = await ann_client.fetch_top_k(req.embedding, k=400)",
                "    features = await hydrator.fetch_online(candidates, timeout_ms=3.0)",
                "    if req.elapsed_ms() > budget_ms * 0.65:",
                "        return fast_linear_fallback(candidates, features)",
                "    return await cross_encoder.rank(candidates, features)",
            ],
            "highlight_lines": [4, 5],
            "side_panel_title": "BENCHMARK",
            "side_panel_items": [
                "P50 Latency: 6.4ms",
                "P99 Latency: 14.1ms",
                "Circuit-Breaker: ARMED",
            ],
            "status_text": "24/24 TESTS PASSED",
            "status_tone": "pass",
            "source": "Latency Fallback Guard",
        },
        2.5,
    ),
)


def probe_video(mp4_path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH")
    raw = subprocess.check_output(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_frames:format=duration",
            "-of",
            "json",
            str(mp4_path),
        ],
        text=True,
    )
    data = json.loads(raw)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return {
        "codec_name": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "pix_fmt": stream.get("pix_fmt"),
        "r_frame_rate": stream.get("r_frame_rate"),
        "nb_frames": int(stream.get("nb_frames") or 0),
        "duration": float(fmt.get("duration") or 0.0),
    }


def verify_all_templates(
    out_dir: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    reg = TemplateRegistry(width=width, height=height, fps=fps)
    results: list[dict[str, Any]] = []
    try:
        for slug, template_id, props, dur in VERIFICATION_CASES:
            mp4_path = out_dir / f"{slug}.mp4"
            png_path = out_dir / f"{slug}_mid.png"

            audit = reg.inspect_layout(template_id, props, duration_sec=dur)
            if not audit.ok:
                raise AssertionError(
                    f"Template {slug} ({template_id}) failed layout check: "
                    f"overflows={audit.overflows}, safe_zone={audit.safe_zone_violations}"
                )

            reg.render_template(
                template_id,
                props,
                dur,
                mp4_path,
                mid_screenshot_path=png_path,
            )
            if not png_path.is_file() or png_path.stat().st_size < 1024:
                raise AssertionError(f"Mid-animation PNG missing or empty for {slug}")

            meta = probe_video(mp4_path)
            if meta["width"] != width or meta["height"] != height:
                raise AssertionError(f"{slug}: expected {width}x{height}, got {meta}")
            if meta["pix_fmt"] != "yuv420p":
                raise AssertionError(f"{slug}: expected yuv420p, got {meta['pix_fmt']}")
            if meta["r_frame_rate"] != f"{fps}/1":
                raise AssertionError(f"{slug}: expected {fps}/1 fps, got {meta['r_frame_rate']}")
            if abs(meta["duration"] - dur) > 0.08:
                raise AssertionError(
                    f"{slug}: expected duration ~{dur:.2f}s, got {meta['duration']:.3f}s"
                )

            summary = {
                "slug": slug,
                "template_id": template_id,
                "layout_ok": audit.ok,
                "mp4": str(mp4_path),
                "png": str(png_path),
                "png_bytes": png_path.stat().st_size,
                "mp4_bytes": mp4_path.stat().st_size,
                **meta,
            }
            results.append(summary)
    finally:
        close_motion_browser()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify all 7 motion-graphics templates.")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "output" / "template_verification"),
        help="Directory for rendered verification .mp4 and .png artifacts.",
    )
    args = parser.parse_args(argv)
    rows = verify_all_templates(Path(args.out_dir))
    for r in rows:
        print(
            f"[PASS] {r['slug']} ({r['template_id']}) -> "
            f"{r['width']}x{r['height']} @ {r['r_frame_rate']} {r['pix_fmt']} "
            f"dur={r['duration']:.2f}s frames={r['nb_frames']} layout_ok={r['layout_ok']}"
        )
    print(f"Verified {len(rows)} template renders with 0 overflows and 0 safe-zone violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
