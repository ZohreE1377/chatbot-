"""ابزارهایی که مدل زبانی صدا می‌زند: مشاور لپ‌تاپ، جست‌وجوی محصول، قیمت و موجودی زنده از ووکامرس."""
import json
import os
import re
import time
from pathlib import Path

import httpx
import pandas as pd

DATA = Path(__file__).parent / "data"
WC_BASE = os.environ.get("WC_BASE", "https://plusrayan.ir").rstrip("/")
STORE_API = f"{WC_BASE}/wp-json/wc/store/v1/products"
PRICE_TTL = int(os.environ.get("PRICE_TTL", "300"))  # ثانیه

LAPTOPS: list = []
CATALOG = pd.DataFrame()


def reload():
    """دیتا را دوباره از فایل‌ها بخوان (بعد از همگام‌سازی)."""
    global LAPTOPS, CATALOG
    LAPTOPS = json.loads((DATA / "laptops.json").read_text(encoding="utf-8"))
    CATALOG = pd.read_csv(DATA / "products_clean.csv").fillna("")


reload()

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك‌", "01234567890123456789یک ")


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).translate(_FA).lower()).strip()


# ----------------------------------------------------------- قیمت و موجودی زنده
_cache: dict[int, tuple[float, dict]] = {}
_client = httpx.AsyncClient(timeout=10, headers={"User-Agent": "PlusRayan-Chatbot"})


async def live(parent_ids: list[int]) -> dict[int, dict]:
    """{parent_id: {price_toman, in_stock, url}} ؛ اگر API جواب ندهد، مقدارها None می‌شوند (هیچ‌وقت عدد ساختگی نمی‌دهیم)."""
    now = time.time()
    need = [i for i in dict.fromkeys(parent_ids) if i not in _cache or now - _cache[i][0] > PRICE_TTL]
    for k in range(0, len(need), 60):
        chunk = need[k:k + 60]
        try:
            r = await _client.get(STORE_API, params={"per_page": 100, "include": ",".join(map(str, chunk))})
            r.raise_for_status()
            for p in r.json():
                pr = p.get("prices") or {}
                minor = 10 ** int(pr.get("currency_minor_unit", 0) or 0)
                raw = (pr.get("price_range") or {}).get("min_amount") or pr.get("price")
                v = float(raw) / minor if raw not in (None, "") else None
                _cur["code"] = pr.get("currency_code") or _cur["code"]
                if v is not None and pr.get("currency_code") == "IRR":
                    v /= 10
                _cache[p["id"]] = (now, {"price_toman": int(v) if v else None,
                                         "in_stock": bool(p.get("is_in_stock")), "url": p.get("permalink")})
        except Exception:
            for i in chunk:
                _cache.setdefault(i, (now - PRICE_TTL + 30, {"price_toman": None, "in_stock": None, "url": None}))
    return {i: _cache[i][1] for i in parent_ids if i in _cache}


WC_KEY = os.environ.get("WC_KEY", "")
WC_SECRET = os.environ.get("WC_SECRET", "")
_vcache: dict[int, tuple[float, dict]] = {}
_cur = {"code": None}
_vclient = httpx.AsyncClient(timeout=15, headers={"User-Agent": "PlusRayan-Chatbot"})


async def live_variations(parent_ids) -> dict[int, dict]:
    """{variation_id: {price_toman, in_stock}} برای محصولات متغیر؛ نیاز به WC_KEY/WC_SECRET دارد."""
    out: dict[int, dict] = {}
    if not (WC_KEY and WC_SECRET):
        return out
    now = time.time()

    async def one(pid):
        c = _vcache.get(pid)
        if c and now - c[0] < PRICE_TTL:
            return c[1]
        try:
            r = await _vclient.get(f"{WC_BASE}/wp-json/wc/v3/products/{pid}/variations",
                                   params={"per_page": 100}, auth=(WC_KEY, WC_SECRET))
            r.raise_for_status()
            m = {}
            for v in r.json():
                pr = float(v["price"]) if v.get("price") not in (None, "") else None
                if pr is not None and _cur["code"] == "IRR":
                    pr /= 10
                m[v["id"]] = {"price_toman": int(pr) if pr else None, "in_stock": v.get("stock_status") == "instock"}
            _vcache[pid] = (now, m)
            return m
        except Exception:
            return {}
    import asyncio
    for m in await asyncio.gather(*[one(p) for p in set(parent_ids)]):
        out.update(m)
    return out


FRESH_DAYS = int(os.environ.get("FRESH_DAYS", "180"))


def _has_dates() -> bool:
    return any(d.get("m") for d in LAPTOPS)


def _fresh(d, info) -> bool:
    """موجود ← همیشه معتبر. ناموجود ← فقط اگر در FRESH_DAYS روز اخیر در ووکامرس ویرایش شده (یعنی هنوز نگهداری می‌شود)."""
    if info.get("in_stock") or not _has_dates():
        return True   # بدون تاریخ ویرایش (sync هنوز موفق نشده) نمی‌توانیم قدیمی را تشخیص دهیم؛ حذفی نمی‌کنیم
    m = d.get("m")
    if not m:
        return False
    try:
        days = (time.time() - time.mktime(time.strptime(m[:10], "%Y-%m-%d"))) / 86400
    except Exception:
        return False
    return days <= FRESH_DAYS


def _card(name, pid, row_specs, lv, exact=False):
    lv = lv or {}
    return {
        "name": name,
        "url": lv.get("url") or f"{WC_BASE}/?p={pid}",
        "price_toman": lv.get("price_toman"),
        "in_stock": lv.get("in_stock"),
        "price_exact": bool(exact and lv.get("price_toman")),
        "price_stale": lv.get("in_stock") is False,
        "specs": row_specs,
    }


# ------------------------------------------------------------------ مشاور لپ‌تاپ
def _score(d, a, price, pmax):
    use = a["use"]
    lite = use in ("daily", "office")
    ram = d["r"] or 0
    if lite and (ram < 8 or d["ct"] < 1): return -1
    # قاعده‌ی فروشگاه: گرافیک یکپارچه = «کارهای روزمره»؛ برنامه‌نویسی/طراحی/گیمینگ فقط با گرافیک اختصاصی (کارت MX ضعیف حساب نمی‌شود)
    if use == "dev" and (ram < 16 or d["gt"] < 2 or d["ct"] < 2): return -1
    if use == "design" and (ram < 16 or d["gt"] < 2 or d["ct"] < 2.5): return -1
    if use == "gaming" and d["gt"] < 2: return -1
    cap = (16, 512, 3) if lite else (32, 1024, 9)
    s = min(d["ct"], cap[2]) * 10 + min(ram or 8, cap[0]) / cap[0] * 10 + min(d["s"] or 256, cap[1]) / cap[1] * 5
    if price and pmax:
        s += (14 if lite else (0 if use == "gaming" else 4)) * (1 - price / pmax)
    if use == "gaming": s += d["gt"] * 15
    elif use == "design": s += d["gt"] * 8 + (5 if ram >= 32 else 0)
    elif use == "dev":
        s += (8 if ram >= 16 else -15) + (3 if (d["s"] or 0) >= 512 else -5) + d["ct"] * 6
        if (d["c"] or "").upper().endswith("U"): s -= 10   # کم‌مصرف (U) برای کار سنگین مناسب نیست
    elif d["gt"] >= 2: s -= 4
    if a.get("light"):
        if d["w"] is not None: s += 10 if d["w"] <= 1.5 else (-8 if d["w"] >= 2 else 0)
        else: s += (6 if (d["sc"] or 15.6) <= 14.2 else 0) + (-4 if d["gt"] >= 2 else 0)
    scr = a.get("screen")
    if scr in ("14", "15.6", "16") and d["sc"]:
        x, t = d["sc"], float(scr)
        ok = x <= 14.5 if t == 14 else (14.5 < x <= 15.9 if t == 15.6 else x >= 15.9)
        s += 8 if ok else -6
    if a.get("brand") and norm(d["b"]) == norm(a["brand"]): s += 12
    return s


def _specs(d):
    out = []
    if d["c"]: out.append(f"پردازنده {d['c']}")
    ssd = f"{d['s'] // 1024} ترابایت" if (d["s"] or 0) >= 1024 else f"{d['s']} گیگابایت"
    out.append(f"{d['r']} گیگابایت رم · {ssd} SSD")
    out.append(f"گرافیک اختصاصی {d['g']}" if d["gt"] >= 1 else "گرافیک یکپارچه")
    if d["sc"]: out.append(f"صفحه {d['sc']} اینچ")
    if d["w"]: out.append(f"وزن {d['w']} کیلوگرم")
    return out


async def recommend_laptops(use, budget_toman=None, screen=None, light=False, brand=None, _relaxed=False, **_):
    a = {"use": use, "screen": screen, "light": light, "brand": brand}
    lv = await live([d["p"] for d in LAPTOPS])
    pmax = max([v["price_toman"] or 0 for v in lv.values()] or [0])
    scored = []
    for d in LAPTOPS:
        if not d["ok"]: continue
        info = lv.get(d["p"]) or {}
        pmin = info.get("price_toman")
        if budget_toman and pmin and pmin > budget_toman:
            continue  # حتی ارزان‌ترین تنوع این محصول از بودجه بالاتر است
        if not _fresh(d, info): continue
        sc = _score(d, a, pmin, pmax)
        if sc < 0: continue
        scored.append((sc + (12 if info.get("in_stock") else 0), d, info))
    # حالت «نزدیک‌ترین به بودجه»: ارزان‌ترین گزینه‌های مناسب اول بیایند، نه قوی‌ترین‌ها
    scored.sort(key=(lambda x: ((x[2].get('price_toman') or 10**12), -x[0])) if _relaxed else (lambda x: -x[0]))

    results, seen, i, BATCH = [], set(), 0, 12
    while len(results) < 3 and i < len(scored):
        batch = scored[i:i + BATCH]; i += BATCH
        vp = await live_variations({d["p"] for _, d, _ in batch if d["i"] != d["p"]})
        for sc, d, info in batch:
            if d["p"] in seen: continue
            v = vp.get(d["i"]) if d["i"] != d["p"] else None
            if d["i"] == d["p"]:
                price, stock, exact = info.get("price_toman"), info.get("in_stock"), True
            elif v and v["price_toman"]:
                price, stock, exact = v["price_toman"], v["in_stock"], True
            else:
                price, stock, exact = info.get("price_toman"), info.get("in_stock"), False
            if budget_toman and price and exact and price > budget_toman:
                continue  # این تنوع (مثلاً ۱ ترابایت) از بودجه گران‌تر است؛ تنوع دیگر همین محصول بررسی می‌شود
            seen.add(d["p"])
            results.append(_card(d["n"], d["p"], _specs(d),
                                 {"price_toman": price, "in_stock": stock, "url": info.get("url")}, exact))
            if len(results) == 3: break
    note = ""
    if not results and budget_toman and not _relaxed:
        r = await recommend_laptops(use, None, screen, light, brand, _relaxed=True)
        cards = sorted(r["results"], key=lambda c: c["price_toman"] or 10**12)
        cheapest = cards[0]["price_toman"] if cards else None
        if cheapest and cheapest <= budget_toman * 1.5:
            return {"note": "در بودجه‌ی شما گزینه‌ای پیدا نشد؛ این‌ها کمی بالاتر از بودجه‌اند.", "results": cards}
        # خیلی گران‌تر از بودجه: فقط ارزان‌ترین گزینه را با برچسب «خارج از بودجه» نشان بده
        if cards:
            cards[0]["over_budget"] = True
        return {"note": "در این بودجه هیچ لپ‌تاپ مناسبی برای این کاربرد در فروشگاه نیست"
                        + (f"؛ ارزان‌ترین گزینه‌ی مناسب حدود {cheapest:,} تومان است (کارت آن زیر پیام نمایش داده می‌شود)." if cheapest else "."),
                "results": cards[:1]}
    if len(results) < 3 and budget_toman:
        note = "در این بودجه فقط همین تعداد گزینه‌ی مناسب برای این کاربرد پیدا شد."
    return {"note": note, "results": results}


# ------------------------------------------------------------- جست‌وجوی کاتالوگ
async def search_products(query, limit=5, **_):
    toks = [t for t in norm(query).split() if len(t) > 1]
    if not toks: return {"results": []}
    import math
    hay = (CATALOG["name"] + " " + CATALOG["category_full"] + " " + CATALOG["brand"]).map(norm)
    N = len(hay)
    w = {t: math.log(N / (1 + hay.map(lambda h: t in h).sum())) for t in toks}
    rare = max(toks, key=lambda t: w[t])               # کم‌پیداترین کلمه‌ی پرسش (مثلاً «کیف») باید حتماً باشد
    sc = hay.map(lambda h: sum(w[t] for t in toks if t in h))
    ok = CATALOG[hay.map(lambda h: rare in h)].assign(_c=sc).sort_values("_c", ascending=False)
    ok = ok.drop_duplicates("parent_id").head(limit)
    lv = await live([int(x) for x in ok["parent_id"]])
    return {"results": [_card(r["parent_name"], int(r["parent_id"]),
                              [x for x in (r["category_leaf"], r["brand"]) if x], lv.get(int(r["parent_id"])))
                        for _, r in ok.iterrows()]}


TOOL_SPECS = [
    {"name": "recommend_laptops",
     "description": "بهترین ۱ تا ۳ لپ‌تاپ را بر اساس نیاز مشتری از کاتالوگ فروشگاه پیشنهاد می‌دهد. قیمت و موجودی زنده است. "
                    "هر وقت مشتری برای انتخاب لپ‌تاپ کمک خواست و کاربرد را گفته بود صدایش بزن (بودجه و بقیه اختیاری‌اند).",
     "input_schema": {"type": "object", "properties": {
         "use": {"type": "string", "enum": ["daily", "office", "dev", "design", "gaming"],
                 "description": "daily=دانشجویی/روزمره، office=اداری، dev=برنامه‌نویسی، design=طراحی و رندر، gaming=گیمینگ"},
         "budget_toman": {"type": "integer", "description": "سقف بودجه به تومان"},
         "screen": {"type": "string", "enum": ["14", "15.6", "16", "any"]},
         "light": {"type": "boolean", "description": "سبک بودن مهم است"},
         "brand": {"type": "string", "description": "نام انگلیسی برند، مثل Asus"}},
         "required": ["use"]}},
    {"name": "search_products",
     "description": "جست‌وجوی هر محصول فروشگاه (لوازم جانبی، کیف، پایه، ماوس، مادربرد، پرینتر و…) با کلمه‌های کلیدی فارسی. "
                    "برای لپ‌تاپ‌ها وقتی نیاز مشتری مشخص نیست و فقط مدل خاصی را می‌پرسد هم استفاده می‌شود.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "چند کلمه‌ی کلیدی، مثل «کیف لپ تاپ ۱۵.۶» یا «ماوس بی سیم»"}},
         "required": ["query"]}},
]
TOOL_FUNCS = {"recommend_laptops": recommend_laptops, "search_products": search_products}