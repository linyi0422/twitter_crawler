import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

import httpx
from twscrape.account import TOKEN
from twscrape.api import GQL_FEATURES, GQL_URL, OP_UserByScreenName, OP_UserTweetsAndReplies
from twscrape.models import parse_tweets, parse_user
from twscrape.utils import encode_params, find_obj


def load_cookies_from_db(db_path: Path):
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
    return cookies["auth_token"], cookies["ct0"], row["user_agent"]


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


def post_with_retry(client: httpx.Client, url: str, payload: dict, tries: int = 3) -> httpx.Response:
    last_err = None
    for i in range(tries):
        try:
            rep = client.post(url, json=payload)
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


def get_latest_ops(client: httpx.Client) -> dict:
    # Extract latest query IDs from current web bundle to avoid stale hardcoded ops.
    html = client.get("https://x.com/home").text
    m = re.search(r'src="(https://abs\.twimg\.com/responsive-web/client-web/main\.[^"]+\.js)"', html)
    if not m:
        return {}

    main_js = client.get(m.group(1)).text
    pairs = re.findall(r'queryId:"([A-Za-z0-9_-]+)",operationName:"([^"]+)"', main_js)
    res = {}
    for qid, name in pairs:
        if name in ("UserByScreenName", "UserTweetsAndReplies"):
            res[name] = f"{qid}/{name}"
    return res


def main():
    default_db = Path(__file__).resolve().parents[1] / "state" / "accounts.db"
    parser = argparse.ArgumentParser(description="Crawl one X(Twitter) user timeline via GraphQL (sync).")
    parser.add_argument("--username", required=True, help="Target username without @")
    parser.add_argument("--limit", type=int, default=3200)
    parser.add_argument("--output", default=None)
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--ct0", default=None)
    parser.add_argument("--db", default=str(default_db))
    args = parser.parse_args()

    if args.auth_token and args.ct0:
        auth_token, ct0 = args.auth_token, args.ct0
        user_agent = "Mozilla/5.0"
    else:
        auth_token, ct0, user_agent = load_cookies_from_db(Path(args.db))

    out = Path(args.output or f"{args.username}.jsonl")

    ft_user = {
        "highlights_tweets_tab_ui_enabled": True,
        "hidden_profile_likes_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "hidden_profile_subscriptions_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "subscriptions_verification_info_is_identity_verified_enabled": False,
        "responsive_web_twitter_article_notes_tab_enabled": False,
        "subscriptions_feature_can_gift_premium": False,
        "profile_label_improvements_pcf_label_in_post_enabled": False,
    }

    with make_client(auth_token, ct0, user_agent) as client:
        ops = get_latest_ops(client)
        op_user = ops.get("UserByScreenName", OP_UserByScreenName)
        op_replies = ops.get("UserTweetsAndReplies", OP_UserTweetsAndReplies)

        user_params = encode_params(
            {
                "variables": {"screen_name": args.username, "withSafetyModeUserFields": True},
                "features": {**GQL_FEATURES, **ft_user},
            }
        )
        user_rep = get_with_retry(client, f"{GQL_URL}/{op_user}", user_params)
        if user_rep.status_code != 200:
            raise SystemExit(f"user lookup failed: HTTP {user_rep.status_code} - {user_rep.text[:200]}")

        user = parse_user(user_rep)
        if not user:
            raise SystemExit("cannot parse user from response (check username)")

        seen = set()
        written = 0
        cursor = None

        with out.open("w", encoding="utf-8") as f:
            while written < args.limit:
                vars_tl = {
                    "userId": str(user.id),
                    "count": 40,
                    "includePromotedContent": True,
                    "withCommunity": True,
                    "withVoice": True,
                    "withV2Timeline": True,
                }
                if cursor:
                    vars_tl["cursor"] = cursor

                tl_payload = {"variables": vars_tl, "features": {**GQL_FEATURES}}
                tl_rep = post_with_retry(client, f"{GQL_URL}/{op_replies}", tl_payload)
                if tl_rep.status_code != 200:
                    raise SystemExit(
                        f"timeline request failed: HTTP {tl_rep.status_code} - {tl_rep.text[:200]}"
                    )

                page_items = 0
                for tw in parse_tweets(tl_rep, limit=-1):
                    tid = getattr(tw, "id", None)
                    if tid is None or tid in seen:
                        continue
                    seen.add(tid)
                    if hasattr(tw, "model_dump"):
                        data = tw.model_dump(mode="json")
                    else:
                        data = tw.dict()
                    f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
                    written += 1
                    page_items += 1
                    if written >= args.limit:
                        break

                if page_items == 0:
                    break

                cur = find_obj(tl_rep.json(), lambda x: x.get("cursorType") == "Bottom")
                cursor = cur.get("value") if cur else None
                if not cursor:
                    break

    print(f"done: {written} tweets -> {out.resolve()}")


if __name__ == "__main__":
    main()
