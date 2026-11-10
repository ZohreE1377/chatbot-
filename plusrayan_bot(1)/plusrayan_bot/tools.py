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
TOOLS_VERSION = "v9-search"
# زیر این قیمت (تومان) لپ‌تاپی در فروشگاه نداریم؛ اگر بودجه کمتر بود، ارزان‌ترین‌ها را معرفی می‌کنیم
MIN_LAPTOP_PRICE = int(os.environ.get("MIN_LAPTOP_PRICE", "100000000"))
NEW_DAYS = int(os.environ.get("NEW_DAYS", "90"))
MARKET: dict = {}   # شناسه‌ی تنوع ← قیمت بازار (دیجی‌کالا/ترب) برای محصول‌های ناموجود
MARKET_TTL_DAYS = int(os.environ.get("MARKET_TTL_DAYS", "45"))


def reload():
    """دیتا را دوباره از فایل‌ها بخوان (بعد از همگام‌سازی)."""
    global LAPTOPS, CATALOG, MARKET
    LAPTOPS = json.loads((DATA / "laptops.json").read_text(encoding="utf-8"))
    CATALOG = pd.read_csv(DATA / "products_clean.csv").fillna("")
    f = DATA / "market_prices.json"
    try:
        MARKET = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        MARKET = {}
    if "_build_rank" in globals():   # بعد از sync هم رتبه‌ی تازگی دوباره ساخته شود
        _build_rank()
    # قیمت‌های دستی (اولویت بالاتر): data/manual_prices.json  →  {"شناسه‌ی تنوع": قیمت به تومان, ...}
    mf = DATA / "manual_prices.json"
    try:
        if mf.exists():
            t = mf.stat().st_mtime
            for k, v in json.loads(mf.read_text(encoding="utf-8")).items():
                MARKET[str(k)] = {"p": int(v), "s": "manual", "u": "", "t": t}
    except Exception:
        pass


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


def _old(m) -> bool:
    """آخرین ویرایش محصول بیش از FRESH_DAYS روز (۶ ماه) پیش بوده؟"""
    try:
        return (time.time() - time.mktime(time.strptime(m[:10], "%Y-%m-%d"))) / 86400 > FRESH_DAYS
    except Exception:
        return False


def _market(d):
    """قیمت بازار تازه (دیجی‌کالا/ترب) برای این تنوع، یا None."""
    e = MARKET.get(str(d["i"]))
    if e and e.get("p") and (time.time() - e.get("t", 0)) / 86400 <= MARKET_TTL_DAYS:
        return e
    return None


def _newness(d) -> int:
    """امتیاز تازگی: محصول‌های ۲-۳ ماه اخیر (ترند و قیمت جدید) اولویت دارند.
    با تاریخ ایجاد (بعد از sync)، وگرنه با شناسه‌ی محصول (شناسه‌ی بزرگ‌تر = تازه‌تر)."""
    a = d.get("a")
    if a:
        try:
            days = (time.time() - time.mktime(time.strptime(a[:10], "%Y-%m-%d"))) / 86400
            return 20 if days <= NEW_DAYS else (8 if days <= 2 * NEW_DAYS else 0)
        except Exception:
            pass
    ids = _PIDS
    if not ids:
        return 0
    rank = _PID_RANK.get(d["p"], 0)   # ۰ (قدیمی‌ترین) تا ۱ (جدیدترین)
    return 20 if rank >= 0.8 else (8 if rank >= 0.6 else 0)


_PIDS: list = []
_PID_RANK: dict = {}


def _build_rank():
    global _PIDS, _PID_RANK
    _PIDS = sorted({d["p"] for d in LAPTOPS})
    n = max(len(_PIDS) - 1, 1)
    _PID_RANK = {pid: i / n for i, pid in enumerate(_PIDS)}


def _price_reliable(d, info) -> bool:
    """قیمت قابل اتکا: محصول موجود است (قیمت زنده)، یا ناموجود ولی در ۶ ماه اخیر ویرایش شده.
    قیمت ناموجودِ قدیمی (یا بدون تاریخ ویرایش) ممکن است چند برابر عقب باشد و نباید در مقایسه با بودجه به کار رود."""
    if info.get("in_stock") or _market(d):
        return True
    m = d.get("m")
    return bool(m) and not _old(m)


def _card(name, pid, row_specs, lv, exact=False, m=None, trusted=True):
    lv = lv or {}
    if not trusted:   # ناموجودِ قدیمی بدون قیمت بازار: قیمت سایت قابل اتکا نیست، نمایش داده نمی‌شود
        lv = {**lv, "price_toman": None}
    return {
        "needs_inquiry": bool(lv.get("in_stock") is False or _old(m)),
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


def _norm_budget(b):
    """«۱۲۰ تومن» در گفتار یعنی ۱۲۰ میلیون تومان. عدد کوچک‌تر از ۱۰۰٬۰۰۰ را میلیون حساب می‌کنیم
    (لپ‌تاپ ارزان‌تر از ۱۰۰ هزار تومان وجود ندارد). عدد ۱۰۰ هزار تا ۱۰۰۰ هزار (مثل ۱۲۰٬۰۰۰ = «صد و بیست هزار»)
    هم کوچک‌تر از هر لپ‌تاپی است و میلیونی خوانده می‌شود."""
    try:
        b = int(b)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    if b < 1_000_000:
        b *= 1_000_000 if b < 100_000 else 1000
    return b


async def recommend_laptops(use, budget_toman=None, screen=None, light=False, brand=None, _relaxed=False, **_):
    a = {"use": use, "screen": screen, "light": light, "brand": brand}
    budget_toman = _norm_budget(budget_toman)
    if budget_toman and budget_toman < MIN_LAPTOP_PRICE and not _relaxed:
        # با قیمت‌های جدید زیر این مبلغ لپ‌تاپی نداریم: صریح بگو و ارزان‌ترین‌های مناسب را نشان بده
        r = await recommend_laptops(use, None, screen, light, brand, _relaxed=True)
        cards = sorted(r["results"], key=lambda c: c["price_toman"] or 10**12)[:3]
        for c in cards: c["over_budget"] = True
        return {"note": f"زیر {MIN_LAPTOP_PRICE // 10**6} میلیون تومان در فروشگاه لپ‌تاپی نداریم. "
                        "به کاربر صریح بگو «نداریم» و بعد ارزان‌ترین گزینه‌های مناسب (کارت‌های زیر) را معرفی کن.",
                "results": cards}
    lv = await live([d["p"] for d in LAPTOPS])
    pmax = max([v["price_toman"] or 0 for v in lv.values()] or [0])
    scored = []
    for d in LAPTOPS:
        if not d["ok"]: continue
        info = lv.get(d["p"]) or {}
        mk = _market(d) if info.get("in_stock") is not True else None
        if mk:  # ناموجود: قیمت سایت قدیمی است؛ قیمت بازار جایگزین می‌شود
            info = {**info, "price_toman": mk["p"], "_mk": mk}
        pmin = info.get("price_toman")
        if budget_toman and pmin and pmin > budget_toman:
            continue  # حتی ارزان‌ترین تنوع این محصول از بودجه بالاتر است
        if not _fresh(d, info): continue
        if (budget_toman or _relaxed) and not _price_reliable(d, info):
            continue  # با بودجه فقط قیمت‌های قابل اتکا را مقایسه می‌کنیم
        sc = _score(d, a, pmin, pmax)
        if sc < 0: continue
        scored.append((sc + (12 if info.get("in_stock") else 0) - (0 if _price_reliable(d, info) else 15) + _newness(d), d, info))
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
            mk = info.get("_mk")
            if mk and stock is not True:   # تنوع ناموجود ← قیمت بازار
                price, exact = mk["p"], True
            else:
                mk = None
            if budget_toman and price and exact and price > budget_toman:
                continue  # این تنوع (مثلاً ۱ ترابایت) از بودجه گران‌تر است؛ تنوع دیگر همین محصول بررسی می‌شود
            seen.add(d["p"])
            results.append(_card(d["n"], d["p"], _specs(d),
                                 {"price_toman": price, "in_stock": stock, "url": info.get("url")}, exact, m=d.get("m"), trusted=bool(mk) or _price_reliable(d, {"in_stock": stock}))
                           | ({"price_source": "market"} if mk else {}))
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
_COMPOUND = {"پد", "هولدر", "کاور", "استند", "کیف", "کوله", "پدال"}


async def search_products(query, limit=5, **_):
    toks = [t for t in norm(query).split() if len(t) > 1]
    if not toks: return {"results": []}
    import math
    hay = (CATALOG["name"] + " " + CATALOG["category_full"] + " " + CATALOG["brand"]).map(norm)
    N = len(hay)
    w = {t: math.log(N / (1 + hay.map(lambda h: t in h).sum())) for t in toks}
    rare = max(toks, key=lambda t: w[t])               # کم‌پیداترین کلمه‌ی پرسش (مثلاً «کیف») باید حتماً باشد
    sc = hay.map(lambda h: sum(w[t] for t in toks if t in h))
    ok = CATALOG[hay.map(lambda h: rare in h)].assign(_c=sc, _n=CATALOG["name"].map(norm))
    # «ماوس» نباید «ماوس پد» را بیاورد: اگر نام با کلمه‌ی کلیدی شروع شود و بلافاصله کلمه‌ای از محصول دیگر بیاید، کنار می‌رود
    def other_type(n):
        parts = n.split()
        return bool(parts and parts[0] == rare and len(parts) > 1 and parts[1] in _COMPOUND and parts[1] not in toks)
    ok = ok[~ok["_n"].map(other_type)]
    # نامی که با خود کلمه‌ی کلیدی شروع می‌شود (نوع محصول) از نامی که فقط در وسطش آمده مهم‌تر است
    ok = ok.assign(_c=ok["_c"] + ok["_n"].map(lambda n: 3 if n.split()[:1] == [rare] else 0))
    ok = ok.sort_values("_c", ascending=False).drop_duplicates("parent_id").head(max(limit * 3, 12))
    lv = await live([int(x) for x in ok["parent_id"]])
    # موجود بودن فقط امتیاز کوچکی می‌دهد تا ارتباط با سؤال (مثلاً «گیمینگ») مهم‌تر بماند
    ok = ok.assign(_s=ok["_c"] + ok["parent_id"].map(lambda x: 1.0 if (lv.get(int(x)) or {}).get("in_stock") else 0.0))
    ok = ok.sort_values("_s", ascending=False).head(limit)
    return {"results": [_card(r["parent_name"], int(r["parent_id"]),
                              [x for x in (r["category_leaf"], r["brand"]) if x], lv.get(int(r["parent_id"])))
                        for _, r in ok.iterrows()]}


# ----------------------------------------------------- پیشنهاد لوازم جانبی مکمل (Cross-sell)
_ACC_SLOTS = [   # (برچسب، کلمه‌ی دسته‌بندی، کلمه‌های لازم در نام، فقط برای گیمینگ؟)
    ("کیف / کوله", "کیف لپ تاپ", ("کیف", "کوله"), False),
    ("ماوس", "ماوس", ("ماوس",), False),
    ("پایه‌ی خنک‌کننده", "کول پد", ("خنک", "کول"), True),
    ("پایه / استند لپ‌تاپ", "استند لپ تاپ", ("استند", "پایه"), False),
]


async def suggest_accessories(screen_inch=None, gaming=False, **_):
    """برای لپ‌تاپ انتخاب‌شده: کیف مناسب اندازه، ماوس، پایه (و برای گیمینگ کول‌پد). فقط محصول‌های موجود."""
    try:
        scr = float(screen_inch) if screen_inch else None
    except (TypeError, ValueError):
        scr = None
    picks = []   # (برچسب، ردیف)
    cand = {}
    for label, cat, words, only_gaming in _ACC_SLOTS:
        if only_gaming and not gaming:
            continue
        df = CATALOG[CATALOG["category_full"].str.contains(cat, regex=False)]
        df = df[df["name"].map(lambda n: any(w in str(n) for w in words))].drop_duplicates("parent_id")
        if scr:   # اندازه‌ی سازگار (وقتی در دیتا هست) ← ناسازگار حذف می‌شود
            def ok(v):
                try: return (not str(v).strip()) or float(v) + 0.05 >= scr
                except ValueError: return True
            df = df[df["compat_max_inch"].map(ok)]
        if label == "ماوس":   # گیمینگ ← ماوس گیمینگ، وگرنه ماوس معمولی
            g = df["name"].str.contains("گیمینگ|gaming", case=False, regex=True)
            df = df[g] if gaming else df[~g]
        cand[label] = df
    lv = await live([int(x) for d in cand.values() for x in d["parent_id"]])
    cards = []
    for label, df in cand.items():
        for _, r in df.iterrows():
            info = lv.get(int(r["parent_id"])) or {}
            if info.get("in_stock") is True:   # فقط موجودها؛ ناموجود را پیشنهاد نمی‌دهیم
                c = _card(r["parent_name"], int(r["parent_id"]), [label], info)
                c["slot"] = label
                cards.append(c)
                break
    return {"note": "فقط لوازم موجود پیشنهاد شد؛ اگر برای دسته‌ای نیامده یعنی الان موجود نیست.", "results": cards[:4]}


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
    {"name": "suggest_accessories",
     "description": "لوازم جانبی مکمل (کیف مناسب اندازه، ماوس، پایه، و برای گیمینگ پایه‌ی خنک‌کننده) برای لپ‌تاپی که مشتری انتخاب کرده. "
                    "بعد از معرفی لپ‌تاپ، وقتی مشتری لپ‌تاپی را پسندید یا خودش لوازم جانبی خواست صدایش بزن.",
     "input_schema": {"type": "object", "properties": {
         "screen_inch": {"type": "number", "description": "اندازه‌ی صفحه‌ی لپ‌تاپ انتخاب‌شده، مثل 15.6"},
         "gaming": {"type": "boolean", "description": "لپ‌تاپ گیمینگ است؟"}}}},
]
TOOL_FUNCS = {"recommend_laptops": recommend_laptops, "search_products": search_products,
              "suggest_accessories": suggest_accessories}


_build_rank()
  # بعد از تعریف همه‌ی توابع
