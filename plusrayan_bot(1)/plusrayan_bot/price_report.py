#!/usr/bin/env python3
"""گزارش مقایسه‌ی قیمت (داخلی، فقط برای خودت): قیمت لپ‌تاپ‌های ناموجود/قدیمی سایت را با دیجی‌کالا و ترب مقایسه می‌کند.

اجرا (روی کامپیوتر خودت، بدون VPN):
    python price_report.py --limit 3 --debug        # اول یک تست کوچک
    python price_report.py                          # همه‌ی ناموجودها
    python price_report.py --scope all              # همه‌ی محصولات
    python price_report.py --ids 4369,4380          # فقط چند محصول مادر

خروجی: price_report.csv و price_report.xlsx (کنار همین فایل).
هیچ قیمتی خودکار در سایت عوض نمی‌شود؛ تصمیم با خودت است.
نکته: دیجی‌کالا و ترب API رسمی برای این کار ندارند؛ آدرس‌هایی که استفاده می‌شود همان‌هایی است که خود سایت‌ها صدا می‌زنند
و ممکن است تغییر کنند. درخواست‌ها کند (چند ثانیه فاصله) و کش‌شده‌اند. اگر مسدود شدی، ابزار خودش همان منبع را رها می‌کند.
"""
import argparse
import asyncio
import csv
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

import clean_products as cp
import tools

HERE = Path(__file__).parent
CACHE_FILE = HERE / "price_cache.json"
CACHE_TTL = 24 * 3600
UA = "PlusRayan-PriceCheck/1.0 (internal price review)"
SERIES = r"THINKBOOK|THINKPAD|IDEAPAD|LEGION|LOQ|VICTUS|OMEN|NITRO|PREDATOR|ASPIRE|SWIFT|VIVOBOOK|TUF|ROG|ZENBOOK|EXPERTBOOK|PAVILION|ENVY|ELITEBOOK|PROBOOK|KATANA|CYBORG|MODERN|PRESTIGE|STEALTH|RAIDER|SUMMIT|GRAM|YOGA"


# ------------------------------------------------------------------- استخراج مدل
_CPUISH = re.compile(r"^(I[3579]|R[3579]|U[3579]|ULTRA\d?|CORE\d?|N\d{4}|RTX\d+|GTX\d+|MX\d+|RX\d+|DDR\d.*|LPDDR.*|W\d+(PRO|HOME)|\d+(GB|G|TB|T|SSD|HZ))$")


def model_code(name: str):
    """کد مدل سازنده (مثل P1504CVA)؛ اگر نبود None."""
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{4,}", cp.norm(name)):
        for part in t.upper().strip("-").split("-"):
            if (len(part) >= 5 and re.search(r"[A-Z]", part) and re.search(r"\d", part)
                    and not _CPUISH.match(part) and not re.fullmatch(r"\d+[A-Z]{0,2}", part)
                    and not re.fullmatch(r"\d{4,5}[A-Z]{1,2}", part)):   # 13700H و 1315U پردازنده‌اند
                return part
    return None


def search_query(row):
    code = model_code(row["n"])
    if code:
        return code, code
    m = re.search(SERIES, row["n"].upper())
    cpu = re.search(r"\d{4,5}[A-Z]{0,2}", (row.get("c") or "").upper())
    q = " ".join(x for x in (row.get("b"), m.group(0).title() if m else "", cpu.group(0) if cpu else "") if x)
    return None, q or cp.norm(row["n"])[:60]


def _alnum(s):
    return re.sub(r"[^A-Z0-9]", "", cp.norm(s).upper())


def title_matches(title, row, code):
    t = _alnum(title)
    if code:
        return _alnum(code) in t, "دقیق (کد مدل)"
    m = re.search(SERIES, row["n"].upper())
    cpu = re.search(r"\d{4,5}[A-Z]{0,2}", (row.get("c") or "").upper())
    ok = bool(m and cpu and m.group(0) in t and _alnum(cpu.group(0)) in t)
    return ok, "تقریبی (سری و پردازنده)"


def variant_ok(title, row):
    """True اگر رم/SSD عنوان با تنوع ما یکی باشد؛ None اگر در عنوان نیامده."""
    ram, ssd, _ = cp.parse_storage_ram_from_text(title)
    if ram is None and ssd is None:
        return None
    if ram and row.get("r") and ram != row["r"]: return False
    if ssd and row.get("s") and ssd != row["s"]: return False
    return True


# -------------------------------------------------------------------- منبع‌ها
def parse_digikala(js, unit="rial"):
    out = []
    for p in ((js.get("data") or {}).get("products") or []):
        price = ((p.get("default_variant") or {}).get("price") or {}).get("selling_price")
        if not price or p.get("status") not in (None, "marketable"):
            continue
        uri = (p.get("url") or {}).get("uri") or f"/product/dkp-{p.get('id')}/"
        out.append({"title": p.get("title_fa") or p.get("title_en") or "",
                    "price": int(price) // (10 if unit == "rial" else 1),
                    "url": "https://www.digikala.com" + uri if uri.startswith("/") else uri})
    return out


def parse_torob(js):
    out = []
    for p in js.get("results") or []:
        price = p.get("price")
        if not price:
            continue
        url = p.get("web_client_absolute_url") or p.get("more_info_url") or (f"https://torob.com/p/{p['random_key']}/" if p.get("random_key") else "")
        out.append({"title": p.get("name1") or p.get("name2") or "", "price": int(price), "url": url})
    return out


SOURCES = {
    "digikala": ("https://api.digikala.com/v1/search/", "q", parse_digikala),
    "torob": ("https://api.torob.com/v4/base-product/search/", "q", parse_torob),
}


class Fetcher:
    def __init__(self, transport=None, delay=(2.0, 3.5), debug=False, digikala_unit="rial"):
        self.client = httpx.Client(timeout=25, headers={"User-Agent": UA, "Accept": "application/json"}, transport=transport)
        self.delay, self.debug, self.unit = delay, debug, digikala_unit
        self.cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        self.blocked = {k: 0 for k in SOURCES}
        self.dead = set()

    def search(self, source, q):
        if source in self.dead:
            return None
        key = f"{source}|{q}"
        c = self.cache.get(key)
        if c and time.time() - c["t"] < CACHE_TTL:
            return c["r"]
        url, param, parser = SOURCES[source]
        try:
            r = self.client.get(url, params={param: q, "page": 1} if source == "digikala" else {param: q, "page": 0})
            if self.debug:
                d = HERE / "debug"; d.mkdir(exist_ok=True)
                (d / f"{source}_{re.sub(r'[^A-Za-z0-9]+', '_', q)[:40]}.json").write_text(r.text[:200000], encoding="utf-8")
            if r.status_code in (403, 429):
                self.blocked[source] += 1
                if self.blocked[source] >= 3:
                    print(f"⚠ {source}: چند بار پشت‌سرهم مسدود شد ({r.status_code}). این منبع رها شد.")
                    self.dead.add(source)
                return None
            r.raise_for_status()
            res = parser(r.json(), self.unit) if source == "digikala" else parser(r.json())
            self.blocked[source] = 0
        except Exception as e:
            print(f"⚠ {source} «{q}»: {type(e).__name__}: {str(e)[:80]}")
            return None
        self.cache[key] = {"t": time.time(), "r": res}
        CACHE_FILE.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        if self.delay:
            time.sleep(random.uniform(*self.delay))
        return res


# --------------------------------------------------------------------- محاسبه
def best_for_row(results, row, code):
    """(قیمت، لینک، کیفیت) از لیست نتیجه‌ها برای یک تنوع."""
    if not results:
        return None, "", "نتیجه‌ای نبود"
    exact, loose, mq = [], [], ""
    for r in results:
        ok, mq = title_matches(r["title"], row, code)
        if not ok:
            continue
        v = variant_ok(r["title"], row)
        if v is True: exact.append(r)
        elif v is None: loose.append(r)
    pool, q = (exact, "تنوع دقیق") if exact else ((loose, "تنوع نامشخص") if loose else ([], ""))
    if not pool:
        return None, "", "مدل پیدا نشد"
    b = min(pool, key=lambda r: r["price"])
    return b["price"], b["url"], f"{mq.split(' ')[0]} · {q}"


def build_report(rows_by_parent, wc_info, var_prices, fetcher, sources, log=print):
    out, total = [], len(rows_by_parent)
    for n, (pid, rows) in enumerate(rows_by_parent.items(), 1):
        code, q = search_query(rows[0])
        log(f"[{n}/{total}] {q}")
        res = {s: fetcher.search(s, q) for s in sources}
        for row in rows:
            info = wc_info.get(pid) or {}
            vp = var_prices.get(row["i"])
            mine = (vp or {}).get("price_toman") or info.get("price_toman")
            rec = {"شناسه": row["i"], "محصول مادر": pid, "نام": row["n"], "رم": row["r"], "SSD": row["s"],
                   "موجودی": {True: "موجود", False: "ناموجود"}.get(info.get("in_stock"), "نامشخص"),
                   "آخرین ویرایش": row.get("m") or "", "قیمت شما": mine, "عبارت جست‌وجو": q}
            notes = [] if vp and vp.get("price_toman") else ["قیمت شما = ارزان‌ترین تنوع"]
            prices = []
            for s, label in (("digikala", "دیجی‌کالا"), ("torob", "ترب")):
                p, url, qual = best_for_row(res.get(s), row, code) if res.get(s) is not None else (None, "", "دریافت نشد")
                rec[f"قیمت {label}"], rec[f"لینک {label}"], rec[f"تطبیق {label}"] = p, url, qual
                if p: prices.append(p)
            low = min(prices) if prices else None
            rec["کمترین قیمت بازار"] = low
            rec["اختلاف با بازار (٪)"] = round((mine - low) / low * 100, 1) if (mine and low) else None
            if mine and low and (mine / low > 8 or mine / low < 0.125):
                notes.append("واحد قیمت (ریال/تومان) را بررسی کن")
            rec["توضیح"] = " ؛ ".join(notes)
            out.append(rec)
    return out


def save_report(rows, base: Path):
    if not rows:
        print("ردیفی برای گزارش نبود."); return
    cols = list(rows[0].keys())
    with open(base.with_suffix(".csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook(); ws = wb.active; ws.title = "گزارش قیمت"; ws.sheet_view.rightToLeft = True
        ws.append(cols)
        for c in ws[1]: c.font = Font(bold=True)
        yellow, red, gray = (PatternFill("solid", fgColor=x) for x in ("FFF2CC", "F8CBAD", "EDEDED"))
        di = cols.index("اختلاف با بازار (٪)") + 1
        for r in rows:
            ws.append([r[c] for c in cols])
            d = r["اختلاف با بازار (٪)"]
            fill = gray if d is None else (red if d > 20 else (yellow if d > 10 else None))
            if fill:
                for c in ws[ws.max_row]: c.fill = fill
        ws.freeze_panes = "E2"; ws.auto_filter.ref = ws.dimensions
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 60 if c == "نام" else (14 if not c.startswith("لینک") else 30)
        ws.cell(ws.max_row + 2, 1, "قرمز: قیمت تو بیش از ۲۰٪ گران‌تر از بازار · زرد: ۱۰ تا ۲۰٪ · خاکستری: قیمت بازار پیدا نشد")
        wb.save(base.with_suffix(".xlsx"))
    except ImportError:
        print("openpyxl نصب نیست؛ فقط CSV ساخته شد (pip install openpyxl).")


async def _wc_live(pids, with_vars, rows_by_parent):
    info = await tools.live(pids)
    var = {}
    if with_vars:
        var = await tools.live_variations([p for p in pids if any(r["i"] != r["p"] for r in rows_by_parent[p])])
    return info, var


def main(argv=None, transport=None):

    ap = argparse.ArgumentParser(description="گزارش مقایسه‌ی قیمت با دیجی‌کالا و ترب")
    ap.add_argument("--scope", choices=["stale", "all"], default="stale", help="stale = فقط ناموجودها (پیش‌فرض)")
    ap.add_argument("--ids", default="", help="شناسه‌ی محصولات مادر، با کاما")
    ap.add_argument("--limit", type=int, default=0, help="حداکثر تعداد محصول مادر (برای تست)")
    ap.add_argument("--sources", default="digikala,torob")
    ap.add_argument("--digikala-unit", choices=["rial", "toman"], default="rial")
    ap.add_argument("--out", default=str(HERE / "price_report"))
    ap.add_argument("--debug", action="store_true", help="پاسخ خام سایت‌ها را در پوشه‌ی debug/ ذخیره کن")
    a = ap.parse_args(argv)

    by_parent = {}
    for r in tools.LAPTOPS:
        by_parent.setdefault(r["p"], []).append(r)
    pids = [int(x) for x in a.ids.split(",") if x.strip()] or list(by_parent)
    pids = [p for p in pids if p in by_parent]
    print("در حال خواندن قیمت و موجودی فعلی از سایت…")
    info, var = asyncio.run(_wc_live(pids, True, by_parent))
    if a.scope == "stale" and not a.ids:
        pids = [p for p in pids if (info.get(p) or {}).get("in_stock") is not True]
    if a.limit:
        pids = pids[:a.limit]
    print(f"{len(pids)} محصول مادر بررسی می‌شود" + ("" if a.limit else f" (حدود {len(pids) * 2 * 3 // 60 + 1} دقیقه)"))
    f = Fetcher(transport=transport, delay=None if transport else (2.0, 3.5), debug=a.debug, digikala_unit=a.digikala_unit)
    rows = build_report({p: by_parent[p] for p in pids}, info, var, f, [s for s in a.sources.split(",") if s in SOURCES])
    save_report(rows, Path(a.out))
    found = sum(1 for r in rows if r["کمترین قیمت بازار"])
    print(f"تمام شد: {len(rows)} ردیف، قیمت بازار برای {found} ردیف پیدا شد. فایل: {a.out}.xlsx")
    return rows


if __name__ == "__main__":
    main()
