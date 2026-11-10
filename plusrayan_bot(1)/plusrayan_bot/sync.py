"""همگام‌سازی خودکار کاتالوگ از ووکامرس (REST API، فقط‌خواندنی).
خروجی: data/laptops.json و data/products_clean.csv ← بعد از موفقیت در حافظه‌ی چت‌بات بارگذاری مجدد می‌شود.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pandas as pd

import clean_products as cp

DATA = Path(__file__).parent / "data"
WC_BASE = os.environ.get("WC_BASE", "https://plusrayan.ir").rstrip("/")
WC_KEY = os.environ.get("WC_KEY", "")
WC_SECRET = os.environ.get("WC_SECRET", "")
log = logging.getLogger("uvicorn.error")
state = {"last_ok": None, "last_error": None, "running": False, "laptops": None, "products": None}
_lock = asyncio.Lock()


# --------------------------------------------------------------- خواندن از ووکامرس
async def _get_all(client, path, params=None):
    out, page = [], 1
    while True:
        r = await client.get(f"{WC_BASE}/wp-json/wc/v3/{path}", params={**(params or {}), "per_page": 100, "page": page})
        r.raise_for_status()
        out += r.json()
        if page >= int(r.headers.get("x-wp-totalpages", "1")):
            return out
        page += 1


def _cat_paths(cats):
    byid = {c["id"]: c for c in cats}

    def path(cid):
        names, seen = [], set()
        while cid and cid in byid and cid not in seen:
            seen.add(cid); names.append(byid[cid]["name"]); cid = byid[cid].get("parent")
        return " > ".join(reversed(names))
    return path


def _stock(s):
    return {"instock": "1", "onbackorder": "backorder"}.get(s, "0")


def to_export_df(products, variations_by_parent, cats):
    """JSON ووکامرس -> همان ستون‌های فارسیِ خروجی CSV ووکامرس که clean_products انتظار دارد."""
    path = _cat_paths(cats)
    rows = []

    def attrs(row, lst, single):
        for i, a in enumerate(lst[:cp.MAX_ATTR], 1):
            val = a.get("option") if single else ", ".join(a.get("options") or [])
            if val:
                row[f"نام ویژگی {i}"], row[f"مقدار ویژگی {i}"] = a.get("name"), val

    for p in products:
        if p.get("status") != "publish":
            continue
        typ = "variable" if p.get("type") == "variable" else "simple"
        if p.get("type") not in ("simple", "variable"):
            continue
        row = {"شناسه": p["id"], "نوع": typ, "نام": p.get("name", ""), "منتشر شده": 1,
               "در انبار؟": _stock(p.get("stock_status")),
               "دسته‌ها": ", ".join(path(c["id"]) for c in p.get("categories", [])),
               "توضیح کوتاه": p.get("short_description", ""), "توضیحات": p.get("description", ""),
               "تاریخ ویرایش": p.get("date_modified_gmt") or p.get("date_modified"),
               "تاریخ ایجاد": p.get("date_created_gmt") or p.get("date_created")}
        attrs(row, p.get("attributes", []), single=False)
        rows.append(row)
        for v in variations_by_parent.get(p["id"], []):
            opts = [a.get("option") for a in v.get("attributes", []) if a.get("option")]
            vr = {"شناسه": v["id"], "نوع": "variation", "مادر": f"id:{p['id']}",
                  "نام": (p.get("name", "") + (" - " + ", ".join(opts) if opts else "")),
                  "منتشر شده": 1 if v.get("status", "publish") == "publish" else 0,
                  "در انبار؟": _stock(v.get("stock_status")),
                  "تاریخ ویرایش": max(filter(None, [v.get("date_modified_gmt") or v.get("date_modified"),
                                                   p.get("date_modified_gmt") or p.get("date_modified")]), default=None),
                  "تاریخ ایجاد": p.get("date_created_gmt") or p.get("date_created")}
            attrs(vr, v.get("attributes", []), single=True)
            rows.append(vr)
    df = pd.DataFrame(rows)
    for c in ["مادر", "دسته‌ها", "توضیح کوتاه", "توضیحات", "تاریخ ویرایش", "تاریخ ایجاد"] + [f"{k} ویژگی {i}" for i in range(1, cp.MAX_ATTR + 1) for k in ("نام", "مقدار")]:
        if c not in df.columns:
            df[c] = None
    return df


# ------------------------------------------------------- ساخت دیتای لپ‌تاپ برای چت‌بات و فرم
def cpu_tier(c):
    c = (c or "").upper()
    if not c: return 2.0
    if re.search(r"CELERON|ATHLON|(^|\s)N\d{3,4}", c) and not re.search(r"(I|CORE|ULTRA|R|AI)\s*[3579]", c): return 0.5
    m = re.search(r"(?:ULTRA|CORE|I|RYZEN|R|AI)\s*([3579])", c)
    if not m: return 2.0
    base = {"3": 1, "5": 2, "7": 3, "9": 4}[m.group(1)]
    if re.search(r"\d(HX|HK)\b", c) or c.endswith("HX"): return base + 1
    if re.search(r"\d(H|HS|P)\b", c) or c.endswith(("H", "HS")): return base + 0.5
    return float(base)


def gpu_tier(gpu, gpu_type):
    if gpu_type != "dedicated": return 0
    g = (gpu or "").upper()
    if re.search(r"(?<![A-Z])MX\s*\d{3}", g): return 1
    m = re.search(r"(RTX|GTX|RX)\s*(\d{4})", g)
    if m:
        if m.group(1) == "GTX": return 2
        return 3 if int(m.group(2)) % 1000 >= 70 else 2
    return 2


def build_laptops(res: pd.DataFrame, mod=None, created=None):
    mod = mod or {}
    created = created or {}
    lap = res[res["group"] == "لپ‌تاپ"]
    num = lambda v, cast=float: None if pd.isna(v) else cast(v)
    out = []
    for r in lap.itertuples():
        cpu = r.cpu if isinstance(r.cpu, str) else ""
        gpu = r.gpu if isinstance(r.gpu, str) else ""
        flags = r.review_flags if isinstance(r.review_flags, str) else ""
        out.append(dict(
            i=int(r.id), p=int(r.parent_id), n=r.name, b=r.brand if isinstance(r.brand, str) else "",
            c=cpu, ct=cpu_tier(cpu), r=num(r.ram_gb, int), s=num(r.ssd_gb, int),
            g=gpu if r.gpu_type == "dedicated" else "", gt=gpu_tier(gpu, r.gpu_type),
            sc=num(r.screen_inch), w=num(r.weight_kg), pn=r.panel if isinstance(r.panel, str) else "",
            rt=r.ram_type if isinstance(r.ram_type, str) else "",
            ok=0 if "RAM نامعقول" in flags else 1,
            m=(str(mod.get(int(r.id)) or "")[:10] or None),
            a=(str(created.get(int(r.id)) or "")[:10] or None)))
    return out


# ------------------------------------------------------------------ اجرای کامل
async def run_sync(transport=None):
    """یک دور همگام‌سازی. اگر چیزی عجیب باشد، دیتای قبلی دست‌نخورده می‌ماند."""
    import tools
    if _lock.locked():
        return {"ok": False, "error": "همگام‌سازی دیگری در حال اجرا است"}
    async with _lock:
        state["running"] = True
        try:
            if not (WC_KEY and WC_SECRET):
                raise RuntimeError("WC_KEY و WC_SECRET تنظیم نشده‌اند")
            async with httpx.AsyncClient(auth=(WC_KEY, WC_SECRET), timeout=60, transport=transport) as c:
                cats = await _get_all(c, "products/categories")
                prods = await _get_all(c, "products", {"status": "publish"})
                sem = asyncio.Semaphore(5)

                async def vars_of(p):
                    async with sem:
                        return p["id"], await _get_all(c, f"products/{p['id']}/variations")
                got = await asyncio.gather(*[vars_of(p) for p in prods if p.get("type") == "variable"])
            df = to_export_df(prods, dict(got), cats)
            if df.empty:
                raise RuntimeError("ووکامرس هیچ محصول منتشرشده‌ای نداد")
            res, _ = cp.build_table(df)
            laptops = build_laptops(res, dict(zip(df['شناسه'].astype(int), df['تاریخ ویرایش'])),
                                     dict(zip(df['شناسه'].astype(int), df['تاریخ ایجاد'])))
            # محافظ: اگر تعداد لپ‌تاپ‌ها ناگهان زیر نصف شد، چیزی را عوض نکن
            old = len(tools.LAPTOPS)
            if old and len(laptops) < 0.5 * old:
                raise RuntimeError(f"تعداد لپ‌تاپ از {old} به {len(laptops)} رسید؛ برای احتیاط جایگزین نشد")
            DATA.mkdir(exist_ok=True)
            tmp_j, tmp_c = DATA / "laptops.json.tmp", DATA / "products_clean.csv.tmp"
            tmp_j.write_text(json.dumps(laptops, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            res.to_csv(tmp_c, index=False, encoding="utf-8-sig")
            os.replace(tmp_j, DATA / "laptops.json"); os.replace(tmp_c, DATA / "products_clean.csv")
            tools.reload()
            state.update(last_ok=time.strftime("%Y-%m-%d %H:%M:%S"), last_error=None,
                         laptops=len(laptops), products=len(res))
            log.info("sync موفق: %d لپ‌تاپ، %d ردیف محصول", len(laptops), len(res))
            return {"ok": True, "laptops": len(laptops), "rows": len(res)}
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {e}"
            log.error("sync ناموفق (دیتای قبلی حفظ شد): %s", state["last_error"])
            return {"ok": False, "error": state["last_error"]}
        finally:
            state["running"] = False


async def loop(hours: float):
    await asyncio.sleep(20)  # بعد از بالا آمدن سرور
    while True:
        await run_sync()
        await asyncio.sleep(max(0.25, hours) * 3600)