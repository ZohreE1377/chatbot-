"""ساخت data/market_prices.json از خروجی price_report (CSV).
اجرا:  python build_market_prices.py price_report.csv
قاعده: فقط دیجی‌کالا با تطبیق «دقیق». چون قیمت‌ها بالا رفته‌اند، قیمت بازارِ پایین‌تر از ۸۵٪ قیمت قدیمی سایت
یا بیشتر از ۶ برابر آن را مشکوک (تطبیق اشتباه) می‌دانیم و کنار می‌گذاریم."""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

_CPU = re.compile(r"(?<![a-z0-9])(\d{3,5}(?:hx|hs|h|u|p|x|k)?)(?![a-z0-9])")


def _cpus(text):
    return {c for c in _CPU.findall(str(text).lower().replace("-", " ").replace("_", " ")) if re.search(r"[a-z]$", c)}


def cpu_conflict(name, link):
    """پردازنده‌ی آگهی (مثلاً 13620H) با پردازنده‌ی محصول (13420H) فرق دارد؟"""
    mine, theirs = _cpus(name), _cpus(unquote(str(link)))
    return bool(mine and theirs and not (mine & theirs))


src = Path(sys.argv[1] if len(sys.argv) > 1 else "price_report.csv")
df = pd.read_csv(src, encoding="utf-8-sig")
out, skipped = {}, 0
for _, r in df.iterrows():
    cand = []
    if pd.notna(r["قیمت دیجی‌کالا"]) and str(r["تطبیق دیجی‌کالا"]).startswith("دقیق"):
        cand.append((int(r["قیمت دیجی‌کالا"]), "digikala", r["لینک دیجی‌کالا"]))
    if not cand:
        continue
    p, s, u = cand[0]
    if cpu_conflict(r["نام"], u):
        skipped += 1
        continue
    me = r["قیمت شما"]
    if pd.notna(me) and me and (p < 0.85 * me or p > 6 * me):
        skipped += 1
        continue
    out[str(int(r["شناسه"]))] = {"p": p, "s": s, "u": u, "t": int(time.time())}
dst = Path(__file__).parent / "data" / "market_prices.json"
dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print(f"{len(out)} قیمت بازار ذخیره شد ({skipped} ردیف مشکوک کنار گذاشته شد) ← {dst}")
