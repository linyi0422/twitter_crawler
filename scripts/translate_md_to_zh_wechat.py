import argparse
import csv
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(path: Path, cache: dict[str, str]):
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_report_items(rows: list[dict]) -> list[dict]:
    """
    Support both:
    - export_report.json (post_md_path/source_url/metrics...)
    - batch_report_*.json (md_path/input/title...)
    """
    out = []
    for idx, r in enumerate(rows, start=1):
        if "post_md_path" in r:
            item = dict(r)
            item.setdefault("rank", idx)
            item.setdefault("source_url", r.get("source_url") or "")
            out.append(item)
            continue

        if "md_path" in r:
            if str(r.get("status", "")).lower() not in ("", "ok"):
                continue
            md_path = r.get("md_path") or ""
            if not md_path:
                continue
            meta_author = ""
            meta_path = r.get("meta_path") or ""
            try:
                if meta_path and Path(meta_path).exists():
                    m = json.loads(Path(meta_path).read_text(encoding="utf-8"))
                    meta_author = m.get("author_handle") or ""
            except Exception:
                meta_author = ""

            out.append(
                {
                    "rank": r.get("order") or idx,
                    "tweet_id": r.get("tweet_id") or "",
                    "source_url": r.get("input") or "",
                    "title": r.get("title") or "",
                    "author": meta_author,
                    "view_count": None,
                    "like_count": None,
                    "retweet_count": None,
                    "reply_count": None,
                    "post_md_path": md_path,
                }
            )
    return out


def strip_source_header(md_text: str) -> str:
    """Drop top title + metadata bullets exported by export_article_md.py."""
    lines = md_text.splitlines()
    i = 0
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].lstrip().startswith("- "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).strip() + "\n"


def should_translate_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if re.match(r"^https?://\S+$", s):
        return False
    if re.match(r"^!\[[^\]]*\]\([^)]+\)$", s):
        return False
    if re.match(r"^\[[^\]]+\]\([^)]+\)$", s):
        return False
    if re.match(r"^[-*]\s*$", s):
        return False

    ascii_letters = len(re.findall(r"[A-Za-z]", s))
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", s))
    # Translate if mostly English-like text.
    return ascii_letters >= 6 and ascii_letters > (cjk_chars * 0.9 + 2)


def normalize_for_wechat(md_text: str) -> str:
    lines = md_text.splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append("")
            continue

        if s.startswith("# "):
            out.append("## " + s[2:].strip())
            out.append("")
            continue
        if s.startswith("## "):
            out.append("### " + s[3:].strip())
            out.append("")
            continue

        if not s.startswith(("###", "-", "*", ">", "![", "[")) and len(s) > 120:
            segs = re.split(r"(?<=[。！？；])", s)
            buf = ""
            for seg in segs:
                seg = seg.strip()
                if not seg:
                    continue
                if len(buf) + len(seg) <= 80:
                    buf += seg
                else:
                    if buf:
                        out.append(buf)
                    buf = seg
            if buf:
                out.append(buf)
            out.append("")
            continue

        out.append(line)

    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return normalized + "\n"


class Translator:
    def __init__(
        self,
        provider: str,
        openai_api_key: str,
        openai_base_url: str,
        openai_model: str,
        max_retries: int,
        request_interval: float,
        fallback_public: bool = False,
        timeout: float = 180.0,
    ):
        self.max_retries = max(max_retries, 1)
        self.request_interval = max(request_interval, 0.0)
        self.timeout = timeout
        self.openai_api_key = openai_api_key.strip()
        self.openai_base_url = openai_base_url.rstrip("/")
        self.openai_model = openai_model
        self.fallback_public = fallback_public

        if provider == "auto":
            self.provider = "openai" if self.openai_api_key else "public"
        else:
            self.provider = provider

    def translate_many(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        if self.provider == "openai":
            return self._translate_many_openai(texts)
        return self._translate_many_public(texts)

    def _translate_many_openai(self, texts: list[str]) -> list[str]:
        url = f"{self.openai_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        system_prompt = (
            "你是专业科技译者。将输入 JSON 数组中的每一项英文文本翻译为简体中文。"
            "要求：保留 Markdown 结构、链接 URL、代码片段、变量名、函数名、API 名、产品名、人名、公司名。"
            "不要添加解释。返回 JSON 对象：{\"translations\": [\"...\", ...]}，数量与顺序必须一致。"
        )
        payload = {
            "model": self.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
            ],
        }

        last_err = None
        for i in range(1, self.max_retries + 1):
            try:
                rep = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                rep.raise_for_status()
                data = rep.json()
                content = data["choices"][0]["message"]["content"]
                obj = json.loads(content)
                out = obj.get("translations") or []
                if not isinstance(out, list) or len(out) != len(texts):
                    raise RuntimeError("invalid translations size from OpenAI")
                return [str(x).strip() if str(x).strip() else t for x, t in zip(out, texts)]
            except Exception as e:
                last_err = e
                if i < self.max_retries:
                    time.sleep(0.8 * i)
        if self.fallback_public:
            print(f"[warn] openai batch failed, fallback public translator: {last_err}")
            return self._translate_many_public(texts)
        raise RuntimeError(f"openai translate failed: {last_err}")

    def _translate_many_public(self, texts: list[str]) -> list[str]:
        out = []
        for text in texts:
            chunks = self._split_public_text(text, max_len=260)
            ans_parts = []
            for chunk in chunks:
                ans_parts.append(self._translate_one_public(chunk))
                if self.request_interval > 0:
                    time.sleep(self.request_interval)
            out.append("".join(ans_parts).strip() or text)
            if self.request_interval > 0:
                time.sleep(self.request_interval)
        return out

    def _split_public_text(self, text: str, max_len: int = 260) -> list[str]:
        s = text.strip()
        if len(s) <= max_len:
            return [s]
        parts = re.split(r"(?<=[\.\!\?;:。！？；])\s+", s)
        out = []
        cur = ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if len(cur) + len(p) + (1 if cur else 0) <= max_len:
                cur = f"{cur} {p}".strip()
            else:
                if cur:
                    out.append(cur)
                cur = p
        if cur:
            out.append(cur)
        if not out:
            return [s[i : i + max_len] for i in range(0, len(s), max_len)]
        return out

    def _translate_one_public(self, text: str) -> str:
        s = text.strip()
        if not s:
            return text

        errors = []

        try:
            url = "https://translate.plausibility.cloud/api/v1/en/zh/" + quote(s, safe="")
            rep = requests.get(url, timeout=self.timeout)
            rep.raise_for_status()
            data = rep.json()
            ans = (data.get("translation") or "").strip()
            if ans:
                return ans
            errors.append("plausibility:empty")
        except Exception as e:
            errors.append(f"plausibility:{e}")

        try:
            url = "https://simplytranslate.org/api/translate"
            params = {"engine": "google", "from": "en", "to": "zh-CN", "text": s}
            rep = requests.get(url, params=params, timeout=self.timeout)
            rep.raise_for_status()
            data = rep.json()
            ans = (data.get("translated_text") or "").strip()
            if ans:
                return ans
            errors.append("simplytranslate:empty")
        except Exception as e:
            errors.append(f"simplytranslate:{e}")

        try:
            url = "https://api.mymemory.translated.net/get"
            params = {"q": s, "langpair": "en|zh-CN"}
            rep = requests.get(url, params=params, timeout=self.timeout)
            rep.raise_for_status()
            data = rep.json()
            ans = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            detail = f"{data.get('responseDetails') or ''} {ans}"
            if ans and "YOU USED ALL AVAILABLE FREE TRANSLATIONS" not in detail:
                return ans
            errors.append("mymemory:quota")
        except Exception as e:
            errors.append(f"mymemory:{e}")

        raise RuntimeError("all public translators failed: " + " | ".join(errors[-3:]))


def build_batches(items: list[str], max_items: int = 64, max_chars: int = 15000) -> list[list[str]]:
    batches: list[list[str]] = []
    cur: list[str] = []
    chars = 0
    for s in items:
        size = len(s)
        if cur and (len(cur) >= max_items or chars + size > max_chars):
            batches.append(cur)
            cur = []
            chars = 0
        cur.append(s)
        chars += size
    if cur:
        batches.append(cur)
    return batches


def translate_markdown(
    md_text: str,
    translator: Translator,
    cache: dict[str, str],
    max_workers: int,
) -> str:
    # For OpenAI path, translate full markdown in one request per post to
    # reduce network round-trips and avoid timeout amplification.
    if translator.provider == "openai":
        key = "__md__:" + md_text
        if key in cache:
            return cache[key]
        out = translator.translate_many([md_text])[0].strip() + "\n"
        cache[key] = out
        return out

    lines = md_text.splitlines()
    in_code = False
    needed: list[str] = []
    segments: list[tuple[str, str]] = []  # ("raw"|"trans", text)

    block_lines: list[str] = []
    block_chars = 0
    block_max_chars = 900

    def flush_block():
        nonlocal block_lines, block_chars
        if not block_lines:
            return
        text = "\n".join(block_lines)
        segments.append(("trans", text))
        if text not in cache:
            needed.append(text)
        block_lines = []
        block_chars = 0

    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            flush_block()
            segments.append(("raw", line))
            in_code = not in_code
            continue
        if in_code or not should_translate_line(s):
            flush_block()
            segments.append(("raw", line))
            continue

        line_size = len(line) + 1
        if block_lines and block_chars + line_size > block_max_chars:
            flush_block()
        block_lines.append(line)
        block_chars += line_size

    flush_block()

    unique_needed = list(dict.fromkeys(needed))
    if unique_needed:
        batches = build_batches(unique_needed)

        def job(batch: list[str]):
            return batch, translator.translate_many(batch)

        workers = max(1, max_workers)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(job, b) for b in batches]
            for fut in as_completed(futs):
                batch, results = fut.result()
                for src, dst in zip(batch, results):
                    cache[src] = dst

    out_parts = []
    for kind, text in segments:
        if kind == "raw":
            out_parts.append(text)
        else:
            out_parts.append(cache.get(text, text))
    return "\n".join(out_parts).strip() + "\n"


def translate_short_text(text: str, translator: Translator, cache: dict[str, str]) -> str:
    key = (text or "").strip()
    if not key:
        return ""
    if key in cache:
        return cache[key]
    ans = translator.translate_many([key])[0]
    cache[key] = ans
    return ans


def build_wechat_article(meta: dict, zh_body: str, rank: int) -> str:
    title = (meta.get("title") or "").strip() or f"第{rank}篇"
    source_url = meta.get("source_url") or ""
    author = meta.get("author") or ""

    stat_parts = []
    for label, key in [
        ("浏览", "view_count"),
        ("点赞", "like_count"),
        ("转推", "retweet_count"),
        ("评论", "reply_count"),
    ]:
        v = meta.get(key)
        if v is not None:
            stat_parts.append(f"{label}:{v}")
    stat_line = " | ".join(stat_parts)

    preview = (meta.get("preview_text") or "").strip().replace("\n", " ")
    body = normalize_for_wechat(zh_body)
    lines = [
        f"## {rank}. {title}",
        "",
        f"- 原文链接：[{source_url}]({source_url})",
        f"- 作者：`@{author}`" if author else "- 作者：未知",
        f"- 热度：{stat_line}" if stat_line else "- 热度：未知",
        "",
        "### 一句话导读",
        "",
        f"> {preview}" if preview else "> 这是一篇高热度 AI 文章，建议优先阅读核心方法与案例部分。",
        "",
        "### 正文（中文）",
        "",
        body.strip(),
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Translate markdown posts to Chinese and build WeChat publish markdown."
    )
    parser.add_argument("--report-json", required=True, help="Path to export_report.json")
    parser.add_argument("--output-root", required=True, help="Output dir")
    parser.add_argument("--cache-file", default="")

    parser.add_argument("--translator", choices=["auto", "openai", "public"], default="auto")
    parser.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--openai-model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    parser.add_argument(
        "--fallback-public",
        action="store_true",
        help="If OpenAI translation fails, fallback to public translators (slower).",
    )

    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-interval", type=float, default=0.0)
    parser.add_argument("--max-workers", type=int, default=4)

    parser.add_argument("--title", default="AI 热门文章中文精选（公众号发布版）")
    parser.add_argument("--source-label", default="X Articles Trending")
    args = parser.parse_args()

    report_path = Path(args.report_json)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    cache_path = Path(args.cache_file) if args.cache_file else (output_root / "translation_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_cache(cache_path)

    posts = normalize_report_items(load_json(report_path))
    translator = Translator(
        provider=args.translator,
        openai_api_key=args.openai_api_key,
        openai_base_url=args.openai_base_url,
        openai_model=args.openai_model,
        max_retries=args.max_retries,
        request_interval=args.request_interval,
        fallback_public=args.fallback_public,
    )
    print(f"[translator] {translator.provider}")

    zh_report = []
    wechat_sections = []

    for idx, item in enumerate(posts, start=1):
        post_md_path = Path(item["post_md_path"])
        src_md = post_md_path.read_text(encoding="utf-8")
        src_body = strip_source_header(src_md)

        zh_md = translate_markdown(
            src_body,
            translator=translator,
            cache=cache,
            max_workers=max(args.max_workers, 1),
        )
        zh_path = post_md_path.with_suffix(".zh.md")
        zh_path.write_text(zh_md, encoding="utf-8")

        title_zh = translate_short_text((item.get("title") or "").strip(), translator, cache)
        preview_zh = translate_short_text((item.get("preview_text") or "").strip(), translator, cache)

        section_meta = dict(item)
        section_meta["title"] = title_zh or (item.get("title") or "")
        section_meta["preview_text"] = preview_zh or (item.get("preview_text") or "")
        wechat_sections.append(build_wechat_article(section_meta, zh_md, rank=idx))

        zh_item = dict(item)
        zh_item["title_zh"] = section_meta["title"]
        zh_item["preview_text_zh"] = section_meta["preview_text"]
        zh_item["post_md_zh_path"] = str(zh_path.resolve())
        zh_report.append(zh_item)

        save_cache(cache_path, cache)
        print(f"[{idx}/{len(posts)}] translated: {post_md_path.name}")

    zh_report_path = output_root / "export_report_zh.json"
    zh_report_csv = output_root / "export_report_zh.csv"
    save_json(zh_report_path, zh_report)

    fields = [
        "rank",
        "tweet_id",
        "title_zh",
        "author",
        "view_count",
        "like_count",
        "retweet_count",
        "reply_count",
        "source_url",
        "post_md_zh_path",
    ]
    with zh_report_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in zh_report:
            writer.writerow({k: row.get(k, "") for k in fields})

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    merged = [
        f"# {args.title}",
        "",
        f"> 来源：{args.source_label}",
        f"> 生成时间：{now}",
        f"> 篇数：{len(wechat_sections)}",
        "",
        "## 导读",
        "",
        "本版为中文翻译 + 公众号排版优化稿，特点是短段落、小标题、可直接粘贴到公众号编辑器继续细调。",
        "",
        "---",
        "",
    ]
    merged.extend(wechat_sections)
    merged_path = output_root / "wechat_draft_top3_zh_publish.md"
    merged_path.write_text("\n".join(merged), encoding="utf-8")

    pure_lines = [
        "# AI 热门文章中文译稿",
        "",
        f"> 生成时间：{now}",
        "",
        "---",
        "",
    ]
    for i, it in enumerate(zh_report, start=1):
        pure_lines.append(f"## {i}. {it.get('title_zh') or it.get('title')}")
        pure_lines.append(f"- 原文：[{it.get('source_url')}]({it.get('source_url')})")
        pure_lines.append("")
        pure_lines.append(Path(it["post_md_zh_path"]).read_text(encoding="utf-8").strip())
        pure_lines.append("")
        pure_lines.append("---")
        pure_lines.append("")
    pure_path = output_root / "wechat_draft_top3_zh.md"
    pure_path.write_text("\n".join(pure_lines), encoding="utf-8")

    print("done")
    print(f"zh_report_json: {zh_report_path.resolve()}")
    print(f"zh_report_csv: {zh_report_csv.resolve()}")
    print(f"pure_zh_md: {pure_path.resolve()}")
    print(f"wechat_publish_md: {merged_path.resolve()}")


if __name__ == "__main__":
    main()
