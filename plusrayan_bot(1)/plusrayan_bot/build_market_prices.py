"""ساخت data/market_prices.json از خروجی price_report (CSV).
اجرا:  python build_market_prices.py price_report.csv [--as-of 2026-11-09]
قاعده: دیجی‌کالا اولویت دارد و اگر نبود (یا رد شد) ترب با تطبیق «دقیق». تطبیق «دقیق» همیشه پذیرفته می‌شود؛ تطبیق «تقریبی» فقط وقتی که
پردازنده و گرافیک آگهی با محصول یکی باشد (مثلاً RTX4050 با RTX3050 اشتباه گرفته نشود).
در هر حالت، اگر پردازنده یا گرافیک آگهی با محصول فرق داشته باشد، رد می‌شود.
چون قیمت‌ها بالا رفته‌اند، قیمت بازارِ پایین‌تر از ۸۵٪ قیمت قدیمی سایت یا بیشتر از ۶ برابر آن را مشکوک می‌دانیم و کنار می‌گذاریم.
--as-of: تاریخ تهیه‌ی گزارش (پیش‌فرض: همین الان). اگر گزارش چند روز پیش گرفته شده، تاریخ همان روز را بده."""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

_CPU = re.compile(r"(?<![a-z0-9])(\d{3,5}(?:hx|hs|h|u|p|x|k)?)(?![a-z0-9])")
_GPU = re.compile(r"(?<![a-z])(rtx|gtx|rx|mx)[\s\-]*(\d{3,4})(?![0-9])")


def _norm(text):
    return unquote(str(text)).lower().replace("_", " ")


def _cpus(text):
    return {c for c in _CPU.findall(_norm(text).replace("-", " ")) if re.search(r"[a-z]$", c)}


def _gpus(text):
    return {a + b for a, b in _GPU.findall(_norm(text))}


def cpu_conflict(name, link):
    """پردازنده‌ی آگهی (مثلاً 13620H) با پردازنده‌ی محصول (13420H) فرق دارد؟"""
    mine, theirs = _cpus(name), _cpus(link)
    return bool(mine and theirs and not (mine & theirs))


def gpu_conflict(name, link):
    """گرافیک آگهی (مثلاً RTX3050) با گرافیک محصول (RTX4050) فرق دارد؟"""
    mine, theirs = _gpus(name), _gpus(link)
    return bool(mine and theirs and not (mine & theirs))


def specs_agree(name, link):
    """هم پردازنده و هم گرافیک در اسم و آگهی هست و یکی است؟ (شرط پذیرش تطبیق تقریبی)"""
    return bool(_cpus(name) & _cpus(link)) and bool(_gpus(name) & _gpus(link))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", default="price_report.csv")
    ap.add_argument("--as-of", help="تاریخ گزارش به شکل YYYY-MM-DD")
    a = ap.parse_args()
    t = int(time.mktime(time.strptime(a.as_of, "%Y-%m-%d"))) if a.as_of else int(time.time())
    df = pd.read_csv(Path(a.report), encoding="utf-8-sig")
    out, skipped, approx = {}, 0, 0
    for _, r in df.iterrows():
        # نامزدها به ترتیب: دیجی‌کالا (دقیق، یا تقریبی با پردازنده و گرافیک یکسان)، بعد ترب (فقط دقیق)
        cand = []
        if pd.notna(r["قیمت دیجی‌کالا"]):
            q = str(r["تطبیق دیجی‌کالا"])
            if q.startswith("دقیق") or (q.startswith("تقریبی") and specs_agree(r["نام"], r["لینک دیجی‌کالا"])):
                cand.append((int(r["قیمت دیجی‌کالا"]), "digikala", r["لینک دیجی‌کالا"], q.startswith("دقیق")))
        if "قیمت ترب" in df.columns and pd.notna(r["قیمت ترب"]) and str(r["تطبیق ترب"]) == "دقیق · تنوع دقیق":
            cand.append((int(r["قیمت ترب"]), "torob", r["لینک ترب"], True))
        me = r["قیمت شما"]
        picked = None
        for p, src, u, exact in cand:
            if cpu_conflict(r["نام"], u) or gpu_conflict(r["نام"], u):
                continue
            if pd.notna(me) and me and (p < 0.85 * me or p > 6 * me):
                continue
            picked = (p, src, u, exact)
            break
        if not picked:
            skipped += bool(cand)
            continue
        p, src, u, exact = picked
        approx += 0 if exact else 1
        out[str(int(r["شناسه"]))] = {"p": p, "s": src, "u": u, "t": t}
    dst = Path(__file__).parent / "data" / "market_prices.json"
    dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} قیمت بازار ذخیره شد ({approx} تطبیق تقریبی پذیرفته شد، {skipped} ردیف مشکوک کنار گذاشته شد) ← {dst}")


if __name__ == "__main__":
    main()
