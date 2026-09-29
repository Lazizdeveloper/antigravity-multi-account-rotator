#!/usr/bin/env python3
import os
import sys
import time
import glob
import json
import subprocess
import threading
import re
import signal

LOG_DIRS = [
    os.path.expanduser("~/.gemini/antigravity-cli/log"),
    os.path.expanduser("~/.gemini/antigravity/log")
]
SWITCH_SCRIPT = os.path.expanduser("~/.local/bin/switch-acc")
QUOTAS_CACHE_FILE = os.path.expanduser("~/.gemini/accounts/quotas.json")
RESTART_DIR = "/tmp/agy_auto_rotate"
os.makedirs(RESTART_DIR, exist_ok=True)

# Faqat haqiqiy xato qatorlarini tekshirish
QUOTA_PATTERNS = [
    re.compile(r"errorreport\.go.*RESOURCE_EXHAUSTED.*code 429", re.IGNORECASE),
    re.compile(r"errorreport\.go.*Individual quota reached", re.IGNORECASE),
    re.compile(r"RESOURCE_EXHAUSTED \(code 429\): Individual quota reached", re.IGNORECASE)
]

last_log_activity = time.time()

def get_log_file_email(fpath):
    """Log fayli qaysi hisobga tegishli ekanligini aniqlash."""
    try:
        with open(fpath, "r", errors="ignore") as fp:
            for line in fp:
                if "authenticated successfully as " in line:
                    m = re.search(r"authenticated successfully as ([^ \n\r]+)", line)
                    if m:
                        return m.group(1).strip()
    except Exception:
        pass
    return None

def mark_account_exhausted(email):
    """Limitga uchragan hisobni keshda 0% qilib belgilash."""
    try:
        if os.path.exists(QUOTAS_CACHE_FILE):
            with open(QUOTAS_CACHE_FILE, "r") as f:
                data = json.load(f)
            if email in data.get("accounts", {}):
                data["accounts"][email]["gemini_5hour_pct"] = 0
                data["accounts"][email]["updated_at"] = time.time()
                with open(QUOTAS_CACHE_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"📉 [Auto-Rotator] '{email}' hisobi limiti keshda 0% deb belgilandi.", flush=True)
    except Exception as e:
        print(f"Keshni yangilashda xatolik: {e}", flush=True)

def get_process_for_log(fpath):
    """Log fayli qaysi agy jarayoniga tegishli ekanligini aniqlash."""
    real_fpath = os.path.realpath(fpath)
    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            if os.path.realpath(fd_path) == real_fpath:
                pid = int(fd_path.split("/")[2])
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as cf:
                        cmd = cf.read().decode(errors="ignore")
                        if "agy" in cmd:
                            return pid
                except Exception:
                    pass
        except Exception:
            pass
    return None

def trigger_auto_restart(fpath):
    """Log fayliga tegishli agy sessiyasini yangi hisob bilan avtomatik davom ettirish."""
    agy_pid = get_process_for_log(fpath)
    if not agy_pid:
        return False

    wrapper_pid = None
    # 1. Atrof-muhit o'zgaruvchisi orqali topish
    try:
        with open(f"/proc/{agy_pid}/environ", "rb") as ef:
            for item in ef.read().split(b"\0"):
                if item.startswith(b"AGY_WRAPPER_PID="):
                    wrapper_pid = int(item.split(b"=")[1])
                    break
    except Exception:
        pass

    # 2. PPID orqali topish
    if not wrapper_pid:
        try:
            with open(f"/proc/{agy_pid}/stat", "r") as sf:
                ppid = int(sf.read().split()[3])
                if os.path.exists(f"{RESTART_DIR}/active_{ppid}"):
                    wrapper_pid = ppid
        except Exception:
            pass

    if wrapper_pid:
        print(f"🎯 Wrapper topildi (PID {wrapper_pid}). Sessiya avtomatik qayta ulanmoqda...", flush=True)
        try:
            with open(f"{RESTART_DIR}/restart_{wrapper_pid}", "w") as rf:
                rf.write("1")
            # Jarayonga SIGINT (Ctrl+C) yuborish
            os.kill(agy_pid, signal.SIGINT)
            return True
        except Exception as e:
            print(f"Jarayonni qayta ishga tushirishda xatolik: {e}", flush=True)
    else:
        print(f"ℹ️ Jarayon (PID {agy_pid}) to'g'ridan-to'g'ri ishga tushirilgan (wrappersiz).", flush=True)
    return False

def background_quota_updater():
    """Har 15 daqiqada fon kvotalarini yangilash."""
    time.sleep(120)
    while True:
        try:
            if time.time() - last_log_activity > 30:
                print("🔄 [Auto-Rotator] Fondagi kvotalarni yangilash boshlandi...", flush=True)
                subprocess.run([SWITCH_SCRIPT, "refresh"], capture_output=True, text=True, timeout=120)
                print("✅ [Auto-Rotator] Fondagi kvotalar yangilandi.", flush=True)
                time.sleep(900)
            else:
                time.sleep(30)
        except Exception as e:
            print(f"Kvota yangilashda xatolik: {e}", flush=True)
            time.sleep(300)

def monitor():
    global last_log_activity
    print("🚀 Gemini Auto-Rotator xizmati ishga tushdi (Multi-Session Log Monitor)...", flush=True)
    last_trigger_time = 0

    updater_thread = threading.Thread(target=background_quota_updater, daemon=True)
    updater_thread.start()

    tracked_files = {}

    while True:
        try:
            now = time.time()
            active_log_paths = set()
            for d in LOG_DIRS:
                if not os.path.exists(d):
                    continue
                for fpath in glob.glob(os.path.join(d, "*.log")):
                    try:
                        mtime = os.path.getmtime(fpath)
                        if now - mtime < 7200: # 2 soat
                            active_log_paths.add(fpath)
                    except OSError:
                        pass

            for fpath in active_log_paths:
                if fpath not in tracked_files:
                    try:
                        f = open(fpath, "r", errors="ignore")
                        f.seek(0, os.SEEK_END)
                        tracked_files[fpath] = f
                        print(f"👀 Yangi log fayl kuzatuvga olindi: {os.path.basename(fpath)}", flush=True)
                    except OSError:
                        pass

            to_remove = []
            for fpath, f in tracked_files.items():
                if fpath not in active_log_paths:
                    try:
                        f.close()
                    except Exception:
                        pass
                    to_remove.append(fpath)
            for r in to_remove:
                del tracked_files[r]

            for fpath, f in list(tracked_files.items()):
                while True:
                    line = f.readline()
                    if not line:
                        break

                    # Foydalanuvchi yozgan chat xabarlarini mutlaqo e'tiborsiz qoldirish!
                    if "HandleUserInput" in line or "input_loop.go" in line:
                        continue

                    last_log_activity = time.time()
                    if any(p.search(line) for p in QUOTA_PATTERNS):
                        if now - last_trigger_time > 15:
                            last_trigger_time = now
                            print(f"\n⚠️ Limit aniqlandi ({os.path.basename(fpath)}): {line.strip()[:100]}", flush=True)
                            
                            # Aynan shu sessiyaning hisobini topib, keshda 0% qilish
                            session_email = get_log_file_email(fpath)
                            if session_email:
                                mark_account_exhausted(session_email)

                            print("🔄 Avtomatik eng maqbul hisobga o'tilmoqda (Smart Switch)...", flush=True)
                            try:
                                res = subprocess.run([SWITCH_SCRIPT, "next"], capture_output=True, text=True)
                                print(res.stdout, flush=True)
                                restarted = trigger_auto_restart(fpath)
                                if restarted:
                                    subprocess.run([
                                        "notify-send",
                                        "-u", "normal",
                                        "-a", "Antigravity Rotator",
                                        "Gemini Pro: Limit tugadi!",
                                        "Yangi hisobga o'tildi va sessiya avtomatik davom ettirilmoqda..."
                                    ], check=False)
                                else:
                                    subprocess.run([
                                        "notify-send",
                                        "-u", "critical",
                                        "-a", "Antigravity Rotator",
                                        "Gemini Pro: Limit tugadi!",
                                        "Keyingi hisobga o'tildi. Qayta ulanish uchun: agy -c"
                                    ], check=False)
                            except Exception as e:
                                print(f"Almashtirishda xatolik: {e}", flush=True)

            time.sleep(0.5)

        except Exception as e:
            time.sleep(1)

if __name__ == "__main__":
    monitor()
