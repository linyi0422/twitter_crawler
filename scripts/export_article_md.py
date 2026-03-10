import argparse
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from twscrape.account import TOKEN
from twscrape.api import GQL_FEATURES, GQL_URL, OP_TweetDetail


def parse_tweet_id(url: str) -> str:
    m = re.search(r"/status/(\d+)", url)
    if not m:
        raise SystemExit("Cannot parse tweet id from URL. Expected .../status/<id>")
    return m.group(1)


def load_cookies_from_db(db_path: Path) -> tuple[str, str, str]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT cookies, user_agent FROM accounts WHERE active = 1 ORDER BY last_used DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit("No active account found in accounts.db")
    cookies = json.loads(row["cookies"])
    if "auth_token" not in cookies or "ct0" not in cookies:
        raise SystemExit("Active account is missing auth_token/ct0")
    return cookies["auth_token"], cookies["ct0"], row["user_agent"] or "Mozilla/5.0"


def make_client(auth_token: str, ct0: str, user_agent: str) -> httpx.Client:
    headers = {
        "authorization": TOKEN,
        "x-csrf-token": ct0,
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "user-agent": user_agent,
    }
    cookies = {"auth_token": auth_token, "ct0": ct0}
    return httpx.Client(headers=headers, cookies=cookies, follow_redirects=True, timeout=30.0)


def get_with_retry(client: httpx.Client, url: str, params: dict, tries: int = 3) -> httpx.Response:
    last_err = None
    for i in range(tries):
        try:
            rep = client.get(url, params=params)
            if rep.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 * (i + 1), 6))
                continue
            return rep
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_err = e
            time.sleep(min(2 * (i + 1), 6))
    if last_err:
        raise last_err
    raise RuntimeError("Request failed with unknown error")


def get_latest_tweet_detail_op(client: httpx.Client) -> str:
    html = client.get("https://x.com/home").text
    m = re.search(r'src="(https://abs\.twimg\.com/responsive-web/client-web/main\.[^"]+\.js)"', html)
    if not m:
        return OP_TweetDetail

    main_js = client.get(m.group(1)).text
    pairs = re.findall(r'queryId:"([A-Za-z0-9_-]+)",operationName:"([^"]+)"', main_js)
    for qid, name in pairs:
        if name == "TweetDetail":
            return f"{qid}/TweetDetail"
    return OP_TweetDetail


def fetch_tweet_detail(client: httpx.Client, tweet_id: str, op_tweet_detail: str) -> dict:
    variables = {
        "focalTweetId": tweet_id,
        "with_rux_injections": True,
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    features = dict(GQL_FEATURES)
    features["articles_preview_enabled"] = True
    features["responsive_web_enhance_cards_enabled"] = True
    field_toggles = {
        "withArticleRichContentState": True,
        "withArticlePlainText": True,
        "withGrokAnalyze": False,
        "withDisallowedReplyControls": False,
    }

    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(features, separators=(",", ":")),
        "fieldToggles": json.dumps(field_toggles, separators=(",", ":")),
    }

    rep = get_with_retry(client, f"{GQL_URL}/{op_tweet_detail}", params)
    if rep.status_code != 200:
        raise SystemExit(f"TweetDetail failed: HTTP {rep.status_code} - {rep.text[:300]}")
    return rep.json()


def walk_find_tweet(node, tweet_id: str):
    if isinstance(node, dict):
        rid = node.get("rest_id")
        if str(rid) == tweet_id and ("legacy" in node or "core" in node):
            return node
        for v in node.values():
            found = walk_find_tweet(v, tweet_id)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = walk_find_tweet(v, tweet_id)
            if found:
                return found
    return None


def ext_from_url(url: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    return ext if ext else ".jpg"


def apply_bold_inline(text: str, inline_ranges: list[dict]) -> str:
    """
    Convert Draft.js BOLD ranges to markdown **...** while preserving text order.

    X Article content uses Draft.js-style inline ranges. We only map BOLD here
    because losing bold hurts readability the most in long-form posts.
    """
    if not text:
        return text

    n = len(text)
    bold_mask = [False] * n
    for r in inline_ranges or []:
        if str(r.get("style") or "").upper() != "BOLD":
            continue
        try:
            start = int(r.get("offset") or 0)
            length = int(r.get("length") or 0)
        except Exception:
            continue
        if length <= 0 or start >= n:
            continue
        start = max(start, 0)
        end = min(start + length, n)
        for i in range(start, end):
            bold_mask[i] = True

    out: list[str] = []
    in_bold = False
    for i, ch in enumerate(text):
        if bold_mask[i] and not in_bold:
            out.append("**")
            in_bold = True
        elif not bold_mask[i] and in_bold:
            out.append("**")
            in_bold = False
        out.append(ch)

    if in_bold:
        out.append("**")
    return "".join(out)


def download_to(client: httpx.Client, url: str, dst: Path) -> bool:
    try:
        rep = client.get(url)
        if rep.status_code != 200:
            return False
        dst.write_bytes(rep.content)
        return True
    except Exception:
        return False


def build_article_markdown(client: httpx.Client, tweet_obj: dict, tweet_id: str, out_dir: Path) -> tuple[Path, int, dict]:
    article = ((tweet_obj.get("article") or {}).get("article_results") or {}).get("result") or {}
    if not article:
        raise SystemExit("No article found in this tweet")

    article_id = str(article.get("rest_id") or article.get("id") or "")
    user = ((tweet_obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    user_legacy = user.get("legacy") or {}
    handle = user_legacy.get("screen_name") or ""
    name = user_legacy.get("name") or ""

    title = article.get("title") or f"Article {article_id or tweet_id}"
    first_pub = (article.get("metadata") or {}).get("first_published_at_secs")
    published_utc = ""
    if first_pub:
        published_utc = datetime.fromtimestamp(int(first_pub), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    tweet_url = f"https://x.com/{handle}/status/{tweet_id}" if handle else f"https://x.com/i/web/status/{tweet_id}"
    article_url = f"https://x.com/i/article/{article_id}" if article_id else ""

    media_url_by_id = {}
    for m in (article.get("media_entities") or []):
        media_id = str(m.get("media_id") or "")
        url = ((m.get("media_info") or {}).get("original_img_url") or "").strip()
        if media_id and url:
            media_url_by_id[media_id] = url
    cover_media = article.get("cover_media") or {}
    cover_id = str(cover_media.get("media_id") or "")
    cover_url = ((cover_media.get("media_info") or {}).get("original_img_url") or "").strip()
    if cover_id and cover_url:
        media_url_by_id[cover_id] = cover_url

    entity_media = {}
    for ent in ((article.get("content_state") or {}).get("entityMap") or []):
        try:
            key = int(ent.get("key"))
        except Exception:
            continue
        mids = []
        for item in (((ent.get("value") or {}).get("data") or {}).get("mediaItems") or []):
            mid = str(item.get("mediaId") or "")
            if mid:
                mids.append(mid)
        if mids:
            entity_media[key] = mids

    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    used_files = {}
    img_index = 1

    def ensure_image(media_id: str):
        nonlocal img_index
        if media_id in used_files:
            return used_files[media_id]
        url = media_url_by_id.get(media_id)
        if not url:
            return None
        fname = f"img_{img_index:02d}_{media_id}{ext_from_url(url)}"
        dst = img_dir / fname
        if not dst.exists():
            ok = download_to(client, url, dst)
            if not ok:
                return None
        used_files[media_id] = fname
        img_index += 1
        return fname

    lines = [f"# {title}", ""]
    lines.append(f"- Author: {name} (@{handle})" if handle else f"- Author: {name}")
    if published_utc:
        lines.append(f"- Published: {published_utc}")
    lines.append(f"- Tweet: {tweet_url}")
    if article_url:
        lines.append(f"- Article: {article_url}")
    lines.append("")

    if cover_id:
        cover_file = ensure_image(cover_id)
        if cover_file:
            lines.append(f"![cover](images/{cover_file})")
            lines.append("")

    blocks = ((article.get("content_state") or {}).get("blocks") or [])
    for b in blocks:
        btype = b.get("type") or "unstyled"
        text = (b.get("text") or "").rstrip()
        text = apply_bold_inline(text, b.get("inlineStyleRanges") or [])
        if btype == "header-one":
            lines.append(f"# {text}")
            lines.append("")
            continue
        if btype == "header-two":
            lines.append(f"## {text}")
            lines.append("")
            continue
        if btype == "header-three":
            lines.append(f"### {text}")
            lines.append("")
            continue
        if btype == "atomic":
            for er in (b.get("entityRanges") or []):
                k = er.get("key")
                if isinstance(k, int):
                    for media_id in entity_media.get(k, []):
                        fn = ensure_image(media_id)
                        if fn:
                            lines.append(f"![{fn}](images/{fn})")
                            lines.append("")
            continue
        if text:
            lines.append(text)
            lines.append("")

    out_md = out_dir / f"{tweet_id}.md"
    out_md.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    meta = {
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
        "article_id": article_id,
        "article_url": article_url,
        "title": title,
        "author_name": name,
        "author_handle": handle,
        "published_utc": published_utc,
        "images_downloaded": len(list(img_dir.glob("*"))),
        "mode": "article",
    }
    return out_md, meta["images_downloaded"], meta


def build_plain_tweet_markdown(client: httpx.Client, tweet_obj: dict, tweet_id: str, out_dir: Path) -> tuple[Path, int, dict]:
    legacy = tweet_obj.get("legacy") or {}
    user = ((tweet_obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    user_legacy = user.get("legacy") or {}
    handle = user_legacy.get("screen_name") or ""
    name = user_legacy.get("name") or ""
    text = legacy.get("full_text") or legacy.get("text") or ""
    created_at = legacy.get("created_at") or ""
    tweet_url = f"https://x.com/{handle}/status/{tweet_id}" if handle else f"https://x.com/i/web/status/{tweet_id}"

    photos = []
    for src in (
        ((legacy.get("extended_entities") or {}).get("media") or []),
        ((legacy.get("entities") or {}).get("media") or []),
    ):
        for m in src:
            if m.get("type") == "photo":
                u = (m.get("media_url_https") or m.get("media_url") or "").strip()
                if u and u not in photos:
                    photos.append(u)

    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for idx, u in enumerate(photos, start=1):
        fn = f"img_{idx:02d}{ext_from_url(u)}"
        dst = img_dir / fn
        if not dst.exists():
            ok = download_to(client, u, dst)
            if not ok:
                continue
        downloaded.append(fn)

    lines = [f"# Tweet {tweet_id}", ""]
    lines.append(f"- Author: {name} (@{handle})" if handle else f"- Author: {name}")
    if created_at:
        lines.append(f"- Created At: {created_at}")
    lines.append(f"- Tweet: {tweet_url}")
    lines.append("")
    if text:
        lines.append(text)
        lines.append("")
    for fn in downloaded:
        lines.append(f"![{fn}](images/{fn})")
        lines.append("")

    out_md = out_dir / f"{tweet_id}.md"
    out_md.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    meta = {
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
        "title": f"Tweet {tweet_id}",
        "author_name": name,
        "author_handle": handle,
        "created_at": created_at,
        "images_downloaded": len(downloaded),
        "mode": "tweet",
    }
    return out_md, len(downloaded), meta


def main():
    default_db = Path(__file__).resolve().parents[1] / "state" / "accounts.db"
    parser = argparse.ArgumentParser(description="Export one X tweet/article to Markdown with images.")
    parser.add_argument("--url", required=True, help="Tweet URL, e.g. https://x.com/.../status/<id>")
    parser.add_argument("--output-root", default=None, help="Default: output/single_post")
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--ct0", default=None)
    parser.add_argument("--db", default=str(default_db))
    parser.add_argument("--save-raw", action="store_true", help="Save raw TweetDetail JSON")
    args = parser.parse_args()

    tweet_id = parse_tweet_id(args.url)
    root = Path(__file__).resolve().parents[1]
    output_root = Path(args.output_root) if args.output_root else (root / "output" / "single_post")
    out_dir = output_root / tweet_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.auth_token and args.ct0:
        auth_token, ct0 = args.auth_token, args.ct0
        user_agent = "Mozilla/5.0"
    else:
        auth_token, ct0, user_agent = load_cookies_from_db(Path(args.db))

    with make_client(auth_token, ct0, user_agent) as client:
        op_tweet_detail = get_latest_tweet_detail_op(client)
        payload = fetch_tweet_detail(client, tweet_id, op_tweet_detail)
        if args.save_raw:
            raw_path = out_dir / f"{tweet_id}_tweetdetail_raw.json"
            raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        tweet_obj = walk_find_tweet(payload, tweet_id)
        if not tweet_obj:
            raise SystemExit("Cannot find focal tweet object in TweetDetail response")

        has_article = bool(((tweet_obj.get("article") or {}).get("article_results") or {}).get("result"))
        if has_article:
            out_md, image_count, meta = build_article_markdown(client, tweet_obj, tweet_id, out_dir)
        else:
            out_md, image_count, meta = build_plain_tweet_markdown(client, tweet_obj, tweet_id, out_dir)

    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done: {out_md.resolve()}")
    print(f"images: {image_count}")
    print(f"meta: {meta_path.resolve()}")


if __name__ == "__main__":
    main()
