#!/usr/bin/env python3
"""RSS news watch: polls feeds every N minutes, tags items by topic, serves a
dashboard + JSON API. Stdlib only. Conditional GETs, jitter, per-feed backoff."""
import hashlib
import html
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config

SGT = timezone(timedelta(hours=8))
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
MAX_ITEMS = 3000
MAX_SEEN = 8000
LOCK = threading.Lock()

STATE = {
    "started": None,
    "items": [],        # newest last
    "seen": [],         # ids seen but not stored (unmatched)
    "acked": [],
    "feeds": {},        # name -> {ok, fail, last_ok, last_error, next_try, matched, etag, lastmod}
}
LOG_PATH = os.environ.get("LOG_PATH", "logs.csv")


def _csv_field(s):
    s = str(s)
    return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s or "\n" in s) else s


def log_event(event, feed="", detail="", backoff=""):
    """Append one line to the run/problems CSV the user can download at /logs.csv."""
    line = ",".join([iso(utcnow()), event, _csv_field(feed), _csv_field(detail), str(backoff)]) + "\n"
    try:
        if not os.path.exists(LOG_PATH):
            with open(LOG_PATH, "w") as f:
                f.write("ts_utc,event,feed,detail,backoff_min\n")
        if os.path.getsize(LOG_PATH) > 1_000_000:
            with open(LOG_PATH) as f:
                lines = f.readlines()
            with open(LOG_PATH, "w") as f:
                f.writelines(lines[:1] + lines[-4000:])
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass
ALERT_RE = re.compile("|".join("(?:%s)" % p for p in config.ALERT_PATTERNS), re.I)
TAG_RE = re.compile(r"<[^>]+>")


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[: len(fmt) + 6], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def localname(tag):
    return tag.rsplit("}", 1)[-1].lower()


def text_of(elem, name):
    for child in elem:
        if localname(child.tag) == name and child.text:
            return child.text.strip()
    return None


BAD_AMP = re.compile(rb'&(?!amp;|lt;|gt;|quot;|apos;|#)')
BAD_CTRL = re.compile(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def parse_feed(data):
    data = BAD_CTRL.sub(b' ', BAD_AMP.sub(b'&amp;', data))
    root = ET.fromstring(data)
    rootname = localname(root.tag)
    entries = []
    if rootname in ("rss", "rdf"):
        channel = root
        if rootname == "rss":
            for child in root:
                if localname(child.tag) == "channel":
                    channel = child
                    break
        for item in channel:
            if localname(item.tag) != "item":
                continue
            title = text_of(item, "title") or "(untitled)"
            link = text_of(item, "link") or ""
            guid = text_of(item, "guid") or link or title
            pub = parse_dt(text_of(item, "pubdate") or text_of(item, "date"))
            desc = text_of(item, "description") or ""
            entries.append({"title": title, "link": link, "guid": guid, "published": pub, "summary": desc})
    elif rootname == "feed":  # Atom
        for entry in root:
            if localname(entry.tag) != "entry":
                continue
            title = text_of(entry, "title") or "(untitled)"
            link = ""
            for l in entry:
                if localname(l.tag) == "link":
                    rel = l.attrib.get("rel", "alternate")
                    if rel == "alternate" or not link:
                        link = l.attrib.get("href", "")
                        if rel == "alternate":
                            break
            guid = text_of(entry, "id") or link or title
            pub = parse_dt(text_of(entry, "published") or text_of(entry, "updated"))
            desc = text_of(entry, "summary") or text_of(entry, "content") or ""
            entries.append({"title": title, "link": link, "guid": guid, "published": pub, "summary": desc})
    return entries


def clean(s, n=300):
    s = TAG_RE.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]


def match_topics(feed, text):
    text = text.lower()
    if feed["mode"] == "all":
        topics = list(feed["topics"])
    else:
        topics = [t for t in feed["topics"]
                  if any(k in text for k in config.TOPICS[t]["keywords"])]
    for t in config.CROSS_MATCH_TOPICS:
        if t not in topics and any(k in text for k in config.TOPICS[t]["keywords"]):
            topics.append(t)
    return topics


def item_id(guid_or_link_or_title):
    return hashlib.sha1(guid_or_link_or_title.encode("utf-8", "replace")).hexdigest()[:16]


def fetch_feed(feed, fs):
    headers = {
        "User-Agent": config.BROWSER_UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    if fs.get("etag"):
        headers["If-None-Match"] = fs["etag"]
    if fs.get("lastmod"):
        headers["If-Modified-Since"] = fs["lastmod"]
    req = urllib.request.Request(feed["url"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            if r.headers.get("ETag"):
                fs["etag"] = r.headers.get("ETag")
            if r.headers.get("Last-Modified"):
                fs["lastmod"] = r.headers.get("Last-Modified")
            return r.read(3_000_000)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None
        raise


def store_item(title, link, guid, summary, published, source, topics, alert):
    with LOCK:
        seen = set(STATE["seen"]) | {i["id"] for i in STATE["items"]}
        iid = item_id(guid or link or title)
        if iid in seen:
            return None
        if not topics:
            STATE["seen"].append(iid)
            return None
        item = {
            "id": iid,
            "title": (title or "(untitled)")[:300],
            "link": link or "",
            "source": source,
            "topics": topics,
            "alert": alert,
            "summary": clean(summary or ""),
            "published": published,
            "matched_at": iso(utcnow()),
        }
        STATE["items"].append(item)
        if len(STATE["items"]) > MAX_ITEMS:
            del STATE["items"][: len(STATE["items"]) - MAX_ITEMS]
        if len(STATE["seen"]) > MAX_SEEN:
            del STATE["seen"][: len(STATE["seen"]) - MAX_SEEN]
        return item


def poll_once():
    total_added = 0
    for feed in config.FEEDS:
        name = feed["name"]
        with LOCK:
            fs = STATE["feeds"].setdefault(name, {
                "ok": 0, "fail": 0, "last_ok": None, "last_error": None,
                "next_try": None, "matched": 0, "etag": None, "lastmod": None})
        if fs["next_try"] and utcnow() < datetime.fromisoformat(fs["next_try"]):
            continue
        try:
            data = fetch_feed(feed, fs)
            entries = parse_feed(data) if data else []
        except Exception as e:
            with LOCK:
                fs["fail"] += 1
                fs["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
                back = min(2 ** min(fs["fail"], 6), 60)  # 2,4,8..60 min backoff
                fs["next_try"] = iso(utcnow() + timedelta(minutes=back))
            log_event("feed_error", name, fs["last_error"], back)
            continue
        recovered = fs["fail"] > 0
        with LOCK:
            fs["ok"] += 1
            fs["last_ok"] = iso(utcnow())
            fs["next_try"] = None
        if recovered:
            log_event("feed_recovered", name)
        added = 0
        for e in entries[:60]:
            text = e["title"] + " " + clean(e["summary"], 400)
            topics = match_topics(feed, text)
            alert = bool(ALERT_RE.search(text)) or bool(feed.get("always_alert"))
            if store_item(e["title"], e["link"], e["guid"], e["summary"],
                          iso(e["published"]) if e["published"] else None,
                          name, topics, alert):
                added += 1
        with LOCK:
            fs["matched"] += added
        total_added += added
        time.sleep(random.uniform(1.0, 2.5))  # politeness jitter between feeds
    log_event("poll_cycle", detail="added=%d" % total_added)
    save_state()


def save_state():
    try:
        tmp = STATE_PATH + ".tmp"
        with LOCK:
            payload = json.dumps(STATE)
        with open(tmp, "w") as f:
            f.write(payload)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        STATE.update({k: v for k, v in data.items() if k in STATE})
    except Exception:
        pass


def poll_loop():
    STATE["started"] = iso(utcnow())
    while True:
        try:
            poll_once()
        except Exception:
            pass
        time.sleep(config.POLL_MINUTES * 60 + random.uniform(0, 45))


def keepalive_loop():
    url = os.environ.get("KEEPALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            req = urllib.request.Request(url + "/api/health",
                                         headers={"User-Agent": config.BROWSER_UA})
            urllib.request.urlopen(req, timeout=15).read(1000)
        except Exception:
            pass


def recent_items(minutes):
    cut = utcnow() - timedelta(minutes=minutes)
    with LOCK:
        return [i for i in STATE["items"]
                if datetime.fromisoformat(i["matched_at"]) >= cut]


def pending_alerts():
    with LOCK:
        acked = set(STATE.get("acked", []))
        return [i for i in STATE["items"] if i["alert"] and i["id"] not in acked]


def feed_health():
    with LOCK:
        out = []
        for feed in config.FEEDS:
            fs = STATE["feeds"].get(feed["name"], {})
            out.append({
                "name": feed["name"],
                "status": "failing" if fs.get("next_try") else "ok",
                "last_ok": fs.get("last_ok"),
                "last_error": fs.get("last_error"),
                "matched": fs.get("matched", 0),
                "fails": fs.get("fail", 0),
            })
        return out


def dashboard():
    items = list(reversed(recent_items(24 * 60)))
    by_topic = {}
    for i in items:
        for t in i["topics"]:
            by_topic.setdefault(t, []).append(i)
    parts = ["""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=120>
<title>Ash News Watch</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
h1{font-size:1.2rem;padding:12px 16px;margin:0;background:#161b22;position:sticky;top:0}
h2{font-size:1rem;padding:10px 16px 4px;margin:0;color:#7ee787}
.meta{color:#8b949e;font-size:.72rem;padding:0 16px 8px}
.item{padding:8px 16px;border-bottom:1px solid #21262d;font-size:.88rem;line-height:1.35}
.item a{color:#58a6ff;text-decoration:none;word-break:break-word}
.src{color:#8b949e;font-size:.72rem}
.alert{display:inline-block;background:#da3633;color:#fff;border-radius:4px;font-size:.65rem;padding:1px 5px;margin-right:5px}
.feeds{font-size:.7rem;color:#8b949e;padding:10px 16px}
.bad{color:#f85149}
</style></head><body>"""]
    started = STATE.get("started") or ""
    parts.append("<h1>Ash News Watch &mdash; every %d min &middot; %d items/24h</h1>" % (config.POLL_MINUTES, len(items)))
    for t, tc in config.TOPICS.items():
        group = by_topic.get(t, [])
        parts.append("<h2>%s (%d)</h2>" % (html.escape(tc["label"]), len(group)))
        for i in group[:40]:
            when = ""
            try:
                when = datetime.fromisoformat(i["matched_at"]).astimezone(SGT).strftime("%d %b %H:%M")
            except Exception:
                pass
            badge = '<span class=alert>ALERT</span>' if i["alert"] else ""
            parts.append('<div class=item>%s<a href="%s">%s</a><br><span class=src>%s &middot; %s SGT</span></div>'
                         % (badge, html.escape(i["link"]), html.escape(i["title"]), html.escape(i["source"]), when))
        if not group:
            parts.append('<div class=meta>nothing yet</div>')
    parts.append('<div class=feeds>')
    for fh in feed_health():
        cls = ' class=bad' if fh["status"] != "ok" else ""
        err = (" &mdash; " + html.escape(fh["last_error"] or "")) if fh["status"] != "ok" else ""
        parts.append('<div%s>%s: %s, %d matched%s</div>' % (cls, html.escape(fh["name"]), fh["status"], fh["matched"], err))
    parts.append("</div><div class=meta>started %s &middot; auto-refresh 2 min</div></body></html>" % html.escape(started[:16]))
    return "".join(parts).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.rstrip("/") == "/api/ingest":
            token = os.environ.get("INGEST_TOKEN", "")
            if not token or self.headers.get("X-Token") != token:
                self._send(json.dumps({"error": "forbidden"}), code=403)
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(n, 500_000)) or b"{}")
            except Exception:
                self._send(json.dumps({"error": "bad json"}), code=400)
                return
            added = 0
            for it in body.get("items", [])[:200]:
                title = str(it.get("title", ""))[:300]
                link = str(it.get("link", ""))[:1000]
                source = str(it.get("source", "external"))[:80]
                topics = [t for t in it.get("topics", []) if t in config.TOPICS]
                pub = it.get("published")
                if isinstance(pub, str):
                    dt = parse_dt(pub)
                    pub = iso(dt) if dt else None
                else:
                    pub = None
                alert = bool(it.get("alert")) or bool(ALERT_RE.search(title))
                if title and link and store_item(title, link, link, "", pub, source, topics, alert):
                    added += 1
            save_state()
            log_event("ingest", detail="added=%d" % added)
            self._send(json.dumps({"ok": True, "added": added}))
        else:
            self._send(json.dumps({"error": "not found"}), code=404)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        if path == "/":
            self._send(dashboard(), "text/html; charset=utf-8")
        elif path == "/api/health":
            self._send(json.dumps({
                "status": "ok", "poll_minutes": config.POLL_MINUTES,
                "started": STATE.get("started"), "items": len(STATE["items"]),
                "feeds": feed_health()}))
        elif path == "/api/matches.json":
            minutes = int(q.get("minutes", ["60"])[0])
            self._send(json.dumps({"items": list(reversed(recent_items(minutes)))}))
        elif path == "/api/alerts/pending":
            self._send(json.dumps({"count": len(pending_alerts()), "items": list(reversed(pending_alerts()))[:50]}))
        elif path == "/api/alerts/ack":
            ids = q.get("ids", [""])[0].split(",")
            with LOCK:
                acked = STATE.setdefault("acked", [])
                if ids == ["all"]:
                    acked.extend(i["id"] for i in STATE["items"] if i["alert"])
                else:
                    acked.extend(i for i in ids if i)
                STATE["acked"] = acked[-2000:]
            save_state()
            self._send(json.dumps({"ok": True, "pending": len(pending_alerts())}))
        elif path == "/api/digest.json":
            minutes = int(q.get("minutes", ["60"])[0])
            items = recent_items(minutes)
            by_topic = {}
            for i in items:
                for t in i["topics"]:
                    by_topic.setdefault(t, []).append(i)
            self._send(json.dumps({
                "minutes": minutes,
                "counts": {t: len(v) for t, v in by_topic.items()},
                "items": {t: [{"title": x["title"], "link": x["link"], "source": x["source"], "alert": x["alert"]}
                              for x in reversed(v)] for t, v in by_topic.items()},
                "feeds": feed_health()}))
        elif path == "/logs.csv":
            try:
                with open(LOG_PATH, "rb") as f:
                    body = f.read()
            except Exception:
                body = b"ts_utc,event,feed,detail,backoff_min\n"
            self._send(body, "text/csv; charset=utf-8")
        elif path == "/api/config":
            self._send(json.dumps({
                "poll_minutes": config.POLL_MINUTES,
                "topics": {t: c["label"] for t, c in config.TOPICS.items()},
                "feeds": [{"name": f["name"], "url": f["url"], "topics": f["topics"], "mode": f["mode"]} for f in config.FEEDS]}))
        else:
            self._send(json.dumps({"error": "not found"}), code=404)


def main():
    load_state()
    log_event("service_start", detail="poll=%dm feeds=%d" % (config.POLL_MINUTES, len(config.FEEDS)))
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
