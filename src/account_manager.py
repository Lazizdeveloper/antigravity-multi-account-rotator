#!/usr/bin/env python3
import json
import os
import sys
import time
import base64
import urllib.request
import urllib.parse
import subprocess
import re

try:
    import secretstorage
except ImportError:
    print("Xatolik: secretstorage moduli topilmadi.")
    sys.exit(1)

ACCOUNTS_DIR = os.path.expanduser("~/.gemini/accounts")
QUOTAS_CACHE_FILE = os.path.join(ACCOUNTS_DIR, "quotas.json")

def get_oauth_credentials():
    """Google Antigravity OAuth client credentials dinamik olish."""
    cid = os.environ.get("GOOGLE_CLIENT_ID")
    cs = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and cs:
        return cid, cs

    client_cfg = os.path.join(ACCOUNTS_DIR, "client.json")
    if os.path.exists(client_cfg):
        try:
            with open(client_cfg, "r") as f:
                d = json.load(f)
                return d.get("client_id"), d.get("client_secret")
        except Exception:
            pass

    # Mahalliy agy binar faylidan dinamik ajratib olish
    agy_bin = os.path.expanduser("~/.local/bin/agy-real")
    if not os.path.exists(agy_bin):
        agy_bin = os.path.expanduser("~/.local/bin/agy")
    if os.path.exists(agy_bin):
        try:
            with open(agy_bin, "rb") as f:
                content = f.read()
            m1 = re.search(rb'[0-9]+-[a-z0-9_]{32}\.apps\.googleusercontent\.com', content)
            m2 = re.search(rb'GOCSPX-[a-zA-Z0-9_-]{28}', content)
            if m1 and m2:
                return m1.group().decode(), m2.group().decode()
        except Exception:
            pass

    return None, None

def get_keyring_item():
    bus = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(bus)
    items = list(collection.search_items({"service": "gemini", "username": "antigravity"}))
    return items[0] if items else None

def get_current_email():
    try:
        item = get_keyring_item()
        if item:
            raw = item.get_secret().decode("utf-8")
            data = json.loads(raw)
            id_token = data.get("id_token", "")
            if id_token:
                payload_b64 = id_token.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                payload = json.loads(base64.b64decode(payload_b64))
                return payload.get("email")
    except Exception:
        pass
    return None

def get_intended_active_email():
    if os.path.exists(ACTIVE_ACC_FILE):
        try:
            with open(ACTIVE_ACC_FILE, "r") as f:
                email = f.read().strip()
                if email:
                    return email
        except Exception:
            pass
    return get_current_email()

def set_intended_active_email(email):
    try:
        with open(ACTIVE_ACC_FILE, "w") as f:
            f.write(email.strip())
    except Exception:
        pass

def refresh_token_if_needed(account_path):
    with open(account_path, "r") as f:
        data = json.loads(f.read())
    
    token_obj = data.get("token", {})
    refresh_token = token_obj.get("refresh_token")
    if not refresh_token:
        return data

    needs_refresh = False
    expiry_str = token_obj.get("expiry")
    if expiry_str:
        try:
            from datetime import datetime, timezone
            exp_clean = expiry_str.split(".")[0]
            if "+" in exp_clean:
                exp_dt = datetime.fromisoformat(expiry_str)
            else:
                exp_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            if exp_dt.timestamp() - time.time() < 300: # 5 daqiqadan kam qolsa
                needs_refresh = True
        except Exception:
            needs_refresh = True
    else:
        needs_refresh = True

    if needs_refresh:
        client_id, client_secret = get_oauth_credentials()
        if not client_id or not client_secret:
            return data
        try:
            req_data = urllib.parse.urlencode({
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/token", data=req_data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode())
                new_access_token = res.get("access_token")
                expires_in = res.get("expires_in", 3600)
                if new_access_token:
                    token_obj["access_token"] = new_access_token
                    from datetime import datetime, timezone, timedelta
                    new_exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    token_obj["expiry"] = new_exp.isoformat()
                    data["token"] = token_obj
                    with open(account_path, "w") as f:
                        f.write(json.dumps(data, indent=2))
        except Exception:
            pass
    return data

def get_accounts():
    files = sorted([f for f in os.listdir(ACCOUNTS_DIR) if f.endswith(".json") and f != "quotas.json"])
    accounts = []
    for f in files:
        email = f.replace(".json", "")
        path = os.path.join(ACCOUNTS_DIR, f)
        accounts.append({"email": email, "path": path, "file": f})
    return accounts

def parse_usage_text(text):
    res = {
        "gemini_weekly_pct": 0,
        "gemini_5hour_pct": 0,
        "claude_gpt_weekly_pct": 0,
        "claude_gpt_5hour_pct": 0,
        "gemini_weekly_reset": "",
        "gemini_5hour_reset": "",
        "claude_gpt_weekly_reset": "",
        "claude_gpt_5hour_reset": ""
    }
    for line in text.splitlines():
        m = re.search(r'(Gemini Models|Claude and GPT models)\s+(Weekly Limit Remaining|Five Hour Limit Remaining)\s+(\d+)%\s*(\S*)', line)
        if m:
            model_grp, limit_type, pct, reset_time = m.groups()
            key_model = 'gemini' if 'Gemini' in model_grp else 'claude_gpt'
            key_type = 'weekly' if 'Weekly' in limit_type else '5hour'
            res[f'{key_model}_{key_type}_pct'] = int(pct)
            res[f'{key_model}_{key_type}_reset'] = reset_time
    return res

def refresh_all_quotas():
    accounts = get_accounts()
    item = get_keyring_item()
    if not item:
        print("Xatolik: Tizim kalitlar omborida (keyring) antigravity yozuvi topilmadi.")
        return None

    # Haqiqiy faol hisobni eslab qolish
    intended_active = get_intended_active_email()
    quotas = {}
    print(f"🔄 Barcha {len(accounts)} ta hisobning jonli kvotasi olinmoqda (kuting)...")

    try:
        for idx, acc in enumerate(accounts, 1):
            email = acc["email"]
            print(f"  [{idx}/{len(accounts)}] {email} tekshirilmoqda...", end="", flush=True)
            t0 = time.time()
            data = refresh_token_if_needed(acc["path"])
            raw = json.dumps(data)
            item.set_secret(raw.encode("utf-8"))

            agy_bin = os.path.expanduser("~/.local/bin/agy-real")
            if not os.path.exists(agy_bin):
                agy_bin = "agy"
            res = subprocess.run([agy_bin, "-p", "/usage"], capture_output=True, text=True, timeout=20)
            elapsed = time.time() - t0

            if res.returncode == 0 and ("Weekly Limit Remaining" in res.stdout or "Quota:" in res.stdout):
                parsed = parse_usage_text(res.stdout)
                parsed["updated_at"] = time.time()
                quotas[email] = parsed
                print(f" ✅ ({elapsed:.1f}s)")
            else:
                print(f" ⚠️ ({elapsed:.1f}s)")
    finally:
        # Har qanday holatda ham faol hisobni o'z o'rniga tiklash
        if intended_active:
            target_path = os.path.join(ACCOUNTS_DIR, f"{intended_active}.json")
            if os.path.exists(target_path):
                data = refresh_token_if_needed(target_path)
                item.set_secret(json.dumps(data).encode("utf-8"))

    cache = {
        "updated_at": time.time(),
        "accounts": quotas
    }
    with open(QUOTAS_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    return cache

def load_quotas(force_refresh=False):
    if force_refresh or not os.path.exists(QUOTAS_CACHE_FILE):
        return refresh_all_quotas()
    try:
        with open(QUOTAS_CACHE_FILE, "r") as f:
            cache = json.load(f)
            if time.time() - cache.get("updated_at", 0) > 10800:
                return refresh_all_quotas()
            return cache
    except Exception:
        return refresh_all_quotas()

def make_bar(pct, width=10):
    pct = max(0, min(100, pct))
    filled = int(round(width * (pct / 100.0)))
    return '[' + '█' * filled + '░' * (width - filled) + f'] {pct:>3}%'

def show_usage(force_refresh=False):
    cache = load_quotas(force_refresh=force_refresh)
    if not cache or "accounts" not in cache:
        print("Xatolik: Kvota ma'lumotlarini yuklab bo'lmadi.")
        return

    current_email = get_current_email()
    accounts = get_accounts()

    print("=" * 88)
    print("🤖 GEMINI PRO MULTI-ACCOUNT KVOTA VA LIMITLAR HOLATI")
    print("=" * 88)
    print(" # | Hisob (Google Account)       | Haftalik (Weekly) | 5-Soatlik Limit  | Holati")
    print("-" * 88)

    usable_count = 0
    total_count = len(accounts)

    for idx, acc in enumerate(accounts, 1):
        email = acc["email"]
        is_active = (email == current_email)
        prefix = "👉" if is_active else f" {idx}"

        data = cache["accounts"].get(email, {})
        weekly_pct = data.get("gemini_weekly_pct", 0)
        fiveh_pct = data.get("gemini_5hour_pct", 0)

        w_bar = make_bar(weekly_pct, 10)
        f_bar = make_bar(fiveh_pct, 10)

        if fiveh_pct > 0 and weekly_pct > 5:
            usable_count += 1

        if is_active:
            status = "🟢 FAOL (Ishlatilmoqda)"
        elif fiveh_pct == 0:
            status = "⏳ 5-soatlik tugagan"
        elif weekly_pct <= 5:
            status = "⚠️ Haftalik limit tugagan"
        else:
            status = "✅ Foydalanishga tayyor"

        print(f"{prefix:>2} | {email:<28} | {w_bar:<17} | {f_bar:<16} | {status}")

    print("=" * 88)
    age_sec = int(time.time() - cache.get("updated_at", time.time()))
    if age_sec < 60:
        age_str = f"{age_sec} soniya oldin"
    else:
        age_str = f"{age_sec // 60} daqiqa oldin"

    print(f"💡 Mavjud tayyor hisoblar: {usable_count}/{total_count} ta  |  Ma'lumot yangiligi: {age_str}")
    print("🔄 Jonli yangilash: switch-acc usage -r  |  Almashtirish: switch-acc <raqam>")
    print("=" * 88)

def list_accounts():
    current_email = get_current_email()
    accounts = get_accounts()
    print("=" * 55)
    print("🤖 ANTIGRAVITY MULTI-ACCOUNT MANAGER (Gemini Pro)")
    print("=" * 55)
    for idx, acc in enumerate(accounts, 1):
        is_active = (acc["email"] == current_email)
        prefix = "👉" if is_active else "  "
        suffix = " [FAOL HISOBLANGAN]" if is_active else ""
        print(f"{prefix} [{idx}] {acc['email']}{suffix}")
    print("=" * 55)
    print("Foydalanish:")
    print("  switch-acc usage        — Barcha hisoblar kvotasini ko'rish")
    print("  switch-acc <raqam>      — Masalan: switch-acc 2")
    print("  switch-acc next         — Eng qulay navbatdagi hisobga o'tish")
    print("=" * 55)

def smart_pick_next_account(accounts, current_email):
    """Eng ko'p kvotasi qolgan va 5-soatlik limiti 0 bo'lmagan hisobni tanlaydi."""
    cache = load_quotas(force_refresh=False)
    candidates = [acc for acc in accounts if acc["email"] != current_email]
    
    if not candidates:
        return accounts[0]

    if cache and "accounts" in cache:
        def score(acc):
            q = cache["accounts"].get(acc["email"], {})
            fiveh = q.get("gemini_5hour_pct", 0)
            weekly = q.get("gemini_weekly_pct", 0)
            if fiveh == 0 or weekly <= 2:
                return -100 + weekly
            return (fiveh * 2) + weekly

        scored = sorted(candidates, key=score, reverse=True)
        best = scored[0]
        if score(best) < 0:
            current_idx = 0
            for i, acc in enumerate(accounts):
                if acc["email"] == current_email:
                    current_idx = i
                    break
            return accounts[(current_idx + 1) % len(accounts)]
        return best

    current_idx = 0
    for i, acc in enumerate(accounts):
        if acc["email"] == current_email:
            current_idx = i
            break
    return accounts[(current_idx + 1) % len(accounts)]

def switch_to_account(target_selector):
    accounts = get_accounts()
    if not accounts:
        print("Xatolik: Hech qanday hisob topilmadi.")
        return False

    current_email = get_current_email()
    target_acc = None

    if target_selector == "next":
        target_acc = smart_pick_next_account(accounts, current_email)
    elif target_selector.isdigit():
        idx = int(target_selector) - 1
        if 0 <= idx < len(accounts):
            target_acc = accounts[idx]
        else:
            print(f"Xato raqam. 1 dan {len(accounts)} gacha tanlang.")
            return False
    else:
        sel = target_selector.lower()
        # 1. To'liq moslik (Exact match)
        for acc in accounts:
            if sel == acc["email"].lower():
                target_acc = acc
                break
        # 2. Prefiks moslik (Prefix / starts with)
        if not target_acc:
            for acc in accounts:
                if acc["email"].lower().startswith(sel):
                    target_acc = acc
                    break
        # 3. Qisman moslik (Substring)
        if not target_acc:
            for acc in accounts:
                if sel in acc["email"].lower():
                    target_acc = acc
                    break

    if not target_acc:
        print(f"'{target_selector}' bo'yicha hisob topilmadi.")
        return False

    # Tokenni yangilash va keyring'ga yozish
    data = refresh_token_if_needed(target_acc["path"])
    raw = json.dumps(data)

    item = get_keyring_item()
    if not item:
        print("Xatolik: Keyring obyekti topilmadi.")
        return False

    item.set_secret(raw.encode("utf-8"))
    set_intended_active_email(target_acc["email"])
    print(f"\n✅ Muvaffaqiyatli o'tildi: {target_acc['email']}")

    try:
        subprocess.run([
            "notify-send",
            "-a", "Antigravity",
            "Gemini Pro hisobi almashtirildi",
            f"Faol hisob: {target_acc['email']}"
        ], check=False)
    except Exception:
        pass

    return True

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        show_usage(force_refresh=False)
    else:
        cmd = args[0].strip().lower()
        if cmd in ("usage", "-u", "--usage", "status"):
            force = ("-r" in args or "--refresh" in args or "refresh" in args)
            show_usage(force_refresh=force)
        elif cmd in ("list", "-l", "--list"):
            list_accounts()
        elif cmd in ("refresh", "-r", "--refresh"):
            show_usage(force_refresh=True)
        else:
            switch_to_account(cmd)
