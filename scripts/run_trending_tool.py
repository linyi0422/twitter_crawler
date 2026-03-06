#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_step(step: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n==> {step}")
    print(">>", subprocess.list2cmdline(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def latest_report_json(batch_reports_dir: Path) -> Path:
    files = sorted(batch_reports_dir.glob("batch_report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"no batch_report_*.json under {batch_reports_dir}")
    return files[0]


def resolve_output_root(root: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command tool: ranking -> markdown export -> Chinese translation -> final bundle"
    )
    parser.add_argument("--run-date", default=datetime.now().strftime("%Y-%m-%d"), help="Hot date, e.g. 2026-03-05")
    parser.add_argument("--run-name", default="", help="Default: top<k>_<YYYYMMDD>")
    parser.add_argument("--output-root", default="output/twitter_trending")
    parser.add_argument("--top-k", type=int, default=3)

    parser.add_argument("--window", default="7d", choices=["24h", "7d", "14d", "all"])
    parser.add_argument("--lang", default="en,zh")
    parser.add_argument("--region", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--sort", default="views")
    parser.add_argument("--api-limit", type=int, default=10000)

    parser.add_argument("--auth-token", required=True)
    parser.add_argument("--ct0", required=True)
    parser.add_argument("--sleep-seconds", type=float, default=0.5)

    parser.add_argument("--translator", choices=["auto", "openai", "public"], default="auto")
    parser.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--openai-model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--fallback-public", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-interval", type=float, default=0.0)
    parser.add_argument("--max-workers", type=int, default=4)

    parser.add_argument("--period-label", default="本周")
    parser.add_argument("--hot-type", default="X（Twitter）长文热门")
    parser.add_argument("--trending-name", default="X Articles Trending")
    parser.add_argument(
        "--reading-advice",
        default="先看第 2 篇（实操），再看第 1 篇（方法论），最后第 3 篇（量化进阶）。",
    )
    parser.add_argument("--cta-line-1", default="公众号会持续更新有深度的推特长文")
    parser.add_argument(
        "--cta-line-2",
        default="如果你是非 AI 技术专业人士，想要把自己的技能移植到自己的工作流中，请加微信",
    )
    parser.add_argument("--wechat-id", default="N742746391")
    parser.add_argument("--clean-output", action="store_true")

    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    output_root = resolve_output_root(root, args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    compact_date = args.run_date.replace("-", "")
    run_name = args.run_name.strip() or f"top{args.top_k}_{compact_date}"
    run_dir = output_root / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    url_file = run_dir / "x_urls.txt"
    markdown_root = run_dir / "markdown_top3"
    zh_draft_root = run_dir / "zh_draft"
    final_dir = run_dir / "final_full_zh"

    if not args.skip_fetch:
        fetch_cmd = [
            sys.executable,
            str(scripts / "fetch_trending_rankings.py"),
            "--window",
            args.window,
            "--lang",
            args.lang,
            "--sort",
            args.sort,
            "--limit",
            str(args.top_k),
            "--api-limit",
            str(args.api_limit),
            "--output-root",
            str(output_root),
            "--run-name",
            run_name,
        ]
        if args.region.strip():
            fetch_cmd += ["--region", args.region.strip()]
        if args.category.strip():
            fetch_cmd += ["--category", args.category.strip()]
        if args.tags.strip():
            fetch_cmd += ["--tags", args.tags.strip()]
        if args.topic.strip():
            fetch_cmd += ["--topic", args.topic.strip()]
        run_step("Fetch X ranking URLs", fetch_cmd, root)

    if not url_file.exists():
        raise SystemExit(f"x_urls.txt not found: {url_file}")

    if not args.skip_export:
        export_cmd = [
            sys.executable,
            str(scripts / "batch_export_x_md.py"),
            "--url-file",
            str(url_file),
            "--output-root",
            str(markdown_root),
            "--auth-token",
            args.auth_token,
            "--ct0",
            args.ct0,
            "--sleep-seconds",
            str(max(args.sleep_seconds, 0.0)),
        ]
        run_step("Export markdown from X URLs", export_cmd, root)

    report_json: Path | None = None
    batch_reports_dir = markdown_root / "batch_reports"
    if batch_reports_dir.exists():
        try:
            report_json = latest_report_json(batch_reports_dir)
        except FileNotFoundError:
            report_json = None

    if not args.skip_translate:
        if not report_json:
            raise SystemExit(f"missing batch report for translation: {batch_reports_dir}")
        translate_cmd = [
            sys.executable,
            str(scripts / "translate_md_to_zh_wechat.py"),
            "--report-json",
            str(report_json.resolve()),
            "--output-root",
            str(zh_draft_root),
            "--translator",
            args.translator,
            "--openai-base-url",
            args.openai_base_url,
            "--openai-model",
            args.openai_model,
            "--max-retries",
            str(max(args.max_retries, 1)),
            "--request-interval",
            str(max(args.request_interval, 0.0)),
            "--max-workers",
            str(max(args.max_workers, 1)),
            "--source-label",
            args.trending_name,
        ]
        if args.openai_api_key.strip():
            translate_cmd += ["--openai-api-key", args.openai_api_key.strip()]
        if args.fallback_public:
            translate_cmd.append("--fallback-public")
        run_step("Translate markdown to Chinese", translate_cmd, root)

    bundle_cmd = [
        sys.executable,
        str(scripts / "build_trending_markdown_bundle.py"),
        "--run-dir",
        str(run_dir),
        "--markdown-root",
        str(markdown_root),
        "--output-dir",
        str(final_dir),
        "--top-k",
        str(max(args.top_k, 1)),
        "--period-label",
        args.period_label,
        "--hot-date",
        args.run_date,
        "--hot-type",
        args.hot_type,
        "--trending-name",
        args.trending_name,
        "--reading-advice",
        args.reading_advice,
        "--cta-line-1",
        args.cta_line_1,
        "--cta-line-2",
        args.cta_line_2,
        "--wechat-id",
        args.wechat_id,
    ]
    if args.clean_output:
        bundle_cmd.append("--clean-output")
    run_step("Build final markdown bundle", bundle_cmd, root)

    summary = {
        "run_name": run_name,
        "run_date": args.run_date,
        "run_dir": str(run_dir.resolve()),
        "url_file": str(url_file.resolve()),
        "markdown_root": str(markdown_root.resolve()),
        "report_json": str(report_json.resolve()) if report_json else "",
        "zh_draft_root": str(zh_draft_root.resolve()),
        "final_dir": str(final_dir.resolve()),
        "final_files": {
            "combined": str((final_dir / "FULL_COMBINED.md").resolve()),
            "guide": str((final_dir / "GUIDE_INTRO.md").resolve()),
        },
    }
    print("\nPipeline done:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
