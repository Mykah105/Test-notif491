import hashlib
import json
import os
import re
import sys
from urllib.parse import urljoin

import requests

TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = "state.json"
UA = {"User-Agent": "Mozilla/5.0"}

AW_BASE = "https://eu.alienwarearena.com"
AW_LIST = AW_BASE + "/esi/featured-tile-data/Giveaway"
AW_FALLBACK = AW_BASE + "/ucf/show/2176399/boards/contest-and-giveaways-global/Giveaway"
AW_HEADERS = {**UA, "X-Requested-With": "XMLHttpRequest"}

AMD_URL = "https://www.amdgaming.com/promotions"
AMD_HEADERS = {**UA, "Accept": "application/json, text/javascript, */*; q=0.01",
               "X-Requested-With": "XMLHttpRequest"}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def notify(title, message, url=None):
    headers = {"Title": title, "Priority": "high"}
    if url:
        headers["Click"] = url
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=message.encode("utf-8"),
                      headers=headers, timeout=20).raise_for_status()
    except Exception as e:
        print("Notification failed:", e)


def handle(state, raw_key, title, count, url):
    key = hashlib.sha1(raw_key.encode()).hexdigest()[:12]
    print(title, "->", count)
    if key not in state and count == 0:
        notify("New giveaway listed", f"{title}: no keys yet", url)
    if count > 0 and not state.get(key):
        notify("Key available!", f"{title}: {count} keys", url)
    state[key] = count > 0


# ---------- Alienware ----------
def aw_list():
    items = []
    try:
        r = requests.get(AW_LIST, headers=AW_HEADERS, timeout=20)
        r.raise_for_status()
        print("AW fields:", sorted(r.json()["data"][0]))
        for g in r.json()["data"][:8]:
            if g.get("url"):
                items.append((urljoin(AW_BASE, g["url"]), g.get("title", "Giveaway")))
    except Exception as e:
        print("Alienware: could not load list:", e)
    return items or [(AW_FALLBACK, "Latest giveaway")]


def aw_total_keys(url):
    r = requests.get(url, headers=AW_HEADERS, timeout=20)
    r.raise_for_status()
    m = re.search(r"var\s+countryKeys\s*=\s*", r.text)
    if not m:
        raise ValueError("countryKeys not found on page")
    data, _ = json.JSONDecoder().raw_decode(r.text[m.end():])
    total = 0
    if isinstance(data, dict):
        for tiers in data.values():
            values = tiers.values() if isinstance(tiers, dict) else tiers
            for v in values:
                try:
                    total += int(v)
                except (TypeError, ValueError):
                    pass
    return total


def check_alienware(state):
    for url, title in aw_list():
        try:
            count = aw_total_keys(url)
        except Exception as e:
            print("Alienware error:", url, "-", e)
            continue
        handle(state, "aw|" + url, title, count, url)


# ---------- AMD ----------
def check_amd(state):
    ctype = "?"
    try:
        r = requests.get(AMD_URL, headers=AMD_HEADERS, timeout=20)
        ctype = r.headers.get("Content-Type", "?")
        r.raise_for_status()
        items = r.json()["items"]
    except Exception as e:
        print("AMD error:", e, "| status/content-type:", ctype)
        return
    print("AMD: read", len(items), "giveaways")
    print("AMD fields:", sorted({k for it in items for k in it}))
    print("AMD statuses:", sorted({str(it.get("status")) for it in items}))
    for it in items:
        if it.get("deleted") or it.get("status") != "active":
            count = 0
        else:
            try:
                count = int(it.get("keysAvailable") or 0)
            except (TypeError, ValueError):
                count = 0
        handle(state, "amd|" + str(it.get("id")), it.get("title", "Giveaway"), count, AMD_URL)


# ---------- Alienware vault timers ----------
VAULT_PAGES = [
    ("Battle Pass Store", AW_BASE + "/battle-store"),
    ("Game Vault", AW_BASE + "/marketplace/game-vault"),
]


def check_vaults(state):
    from datetime import datetime, timezone
    for name, url in VAULT_PAGES:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            m = re.search(r'data-countdown="([^"]+)"', r.text)
            if not m:
                print(name, "-> no timer found")
                continue
            target = datetime.fromisoformat(m.group(1))
            minutes = (target - datetime.now(timezone.utc)).total_seconds() / 60
            print(name, "-> opens in", round(minutes, 1), "min")
            if 0 < minutes <= 6:
                key = hashlib.sha1(("vault|" + url + "|" + m.group(1)).encode()).hexdigest()[:12]
                if not state.get(key):
                    notify(name + " opening soon", f"{name} opens in about {round(minutes)} minutes", url)
                state[key] = True
        except Exception as e:
            print("Vault error:", name, "-", e)


if not TOPIC:
    print("NTFY_TOPIC secret is missing")
    sys.exit(1)


state = load_state()
check_alienware(state)
check_amd(state)
check_vaults(state)
save_state(state)
