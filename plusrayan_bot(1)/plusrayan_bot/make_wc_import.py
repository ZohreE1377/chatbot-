"""ساخت فایل ورود قیمت برای ووکامرس از خروجی price_report.

اجرا:
    python make_wc_import.py price_report.csv --site-unit toman          (یا rial)
گزینه‌ها:
    --margin 0.03     درصد اضافه روی قیمت دیجی‌کالا (۰٫۰۳ = ۳٪ بالاتر). پیش‌فرض ۰
    --round 100000    گرد کردن به نزدیک‌ترین مبلغ (تومان). پیش‌فرض ۱۰۰٬۰۰۰
خروجی:
    woo_import.csv          ← این را در ووکامرس Import کن (فقط قیمت عادی عوض می‌شود)
    woo_import_review.csv   ← قبل از ورود نگاه کن: قیمت قدیم، قیمت جدید، درصد تغییر، لینک دیجی‌کالا

فقط ردیف‌هایی وارد می‌شوند که: دیجی‌کالا، مدل «دقیق» و رم/SSD «دقیق» جور شده باشد،
و قیمت جدید بین ۸۵٪ تا ۶ برابر قیمت قبلی باشد (بقیه مشکوک‌اند و فقط در فایل بررسی با دلیل می‌آیند).
"""
import argparse
import re
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

_CPU = re.compile(r"(?<![a-z0-9])(\d{3,5}(?:hx|hs|h|u|p|x|k)?)(?![a-z0-9])")


def _cpus(text):
    return set(_CPU.findall(str(text).lower().replace("-", " ").replace("_", " ")))


def cpu_conflict(name, link):
    """True اگر در لینک دیجی‌کالا پردازنده‌ی دیگری (مثلاً 13620H به‌جای 13420H) آمده باشد."""
    mine = {c for c in _cpus(name) if re.search(r"[a-z]$", c) and not re.fullmatch(r"\d+(gb|tb|hz|w)", c)}
    theirs = {c for c in _cpus(unquote(str(link))) if re.search(r"[a-z]$", c)}
    return bool(mine and theirs and not (mine & theirs))


ap = argparse.ArgumentParser()
ap.add_argument("report")
ap.add_argument("--site-unit", choices=["toman", "rial"], required=True,
                help="واحد قیمت در خود ووکامرس (مثلاً اگر ادمین ووکامرس «ریال» نشان می‌دهد rial)")
ap.add_argument("--margin", type=float, default=0.0)
ap.add_argument("--round", type=int, default=100_000, dest="rnd")
a = ap.parse_args()

df = pd.read_csv(a.report, encoding="utf-8-sig")
imp, rev = [], []
for _, r in df.iterrows():
    why, new = "", None
    p, me = r["قیمت دیجی‌کالا"], r["قیمت شما"]
    if pd.isna(p):
        why = "قیمت دیجی‌کالا پیدا نشد"
    elif r["تطبیق دیجی‌کالا"] != "دقیق · تنوع دقیق":
        why = f"تطبیق ضعیف ({r['تطبیق دیجی‌کالا']})"
    elif cpu_conflict(r["نام"], r["لینک دیجی‌کالا"]):
        why = "پردازنده‌ی آگهی دیجی‌کالا با محصول تو فرق دارد"
    elif pd.notna(me) and me and (p < 0.85 * me or p > 6 * me):
        why = "اختلاف غیرعادی با قیمت قبلی"
    else:
        new = int(round(p * (1 + a.margin) / a.rnd) * a.rnd)
    var = int(r["شناسه"]) != int(r["محصول مادر"])
    rev.append({"شناسه": r["شناسه"], "نام": r["نام"], "رم": r["رم"], "SSD": r["SSD"],
                "قیمت قبلی (تومان)": me, "قیمت جدید (تومان)": new,
                "تغییر ٪": round((new - me) / me * 100, 1) if (new and pd.notna(me) and me) else None,
                "وارد می‌شود؟": "بله" if new else "خیر", "دلیل": why, "لینک دیجی‌کالا": r["لینک دیجی‌کالا"]})
    if new:
        imp.append({"ID": int(r["شناسه"]), "Type": "variation" if var else "simple",
                    "Parent": f"id:{int(r['محصول مادر'])}" if var else "",
                    "Regular price": new * (10 if a.site_unit == "rial" else 1)})

out = Path(a.report).parent
pd.DataFrame(imp).to_csv(out / "woo_import.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(rev).to_csv(out / "woo_import_review.csv", index=False, encoding="utf-8-sig")
print(f"{len(imp)} ردیف برای ورود از {len(df)} ردیف.  فایل‌ها: woo_import.csv  و  woo_import_review.csv")
print("اول woo_import_review.csv را ببین. بعد از آن، فقط ۲-۳ ردیف را امتحانی وارد کن.")
