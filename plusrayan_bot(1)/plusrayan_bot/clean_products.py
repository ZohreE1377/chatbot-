#!/usr/bin/env python3
"""
تبدیل خروجی CSV ووکامرس به «جدول تمیز مشخصات محصولات» (پلاس رایان)

اجرا:
    python clean_products.py wc-product-export.csv  خروجی_پوشه/

خروجی‌ها:
    products_clean.csv   (برای کد و RAG)
    products_clean.xlsx  (برای بررسی با چشم؛ ردیف‌های مشکوک زرد شده‌اند)

نکته‌ها:
- هر «محصول ساده» و هر «تنوع» یک ردیف می‌گیرد. محصول متغیر خودش ردیف نمی‌گیرد.
- پیش‌نویس و حذف‌شده‌ها وارد نمی‌شوند.
- قیمت و تعداد موجودی عمداً داخل جدول نیست؛ آن‌ها را زنده از ووکامرس بخوان.
- هیچ مدل زبانی یا اینترنتی لازم نیست؛ همه‌چیز با قاعده (regex) استخراج می‌شود.
"""
import html
import re
import sys
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------- کمکی‌ها
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫،", "01234567890123456789.,")
MAX_ATTR = 10


def norm(text) -> str:
    """ارقام فارسی -> انگلیسی، فاصله‌ها یکدست."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = str(text).translate(FA_DIGITS).replace("‌", " ").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def html_to_text(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s = str(raw).replace("\\n", "\n")
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|h[1-6]|li|div|tr)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()[:30000]


def to_gb(text, unitless="gb"):
    """'512G' -> 512 ؛ '1TB' -> 1024 ؛ '16GB(5)' -> 16 ؛ '64+512' -> (512, flag)."""
    s = norm(text).lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # «32GB(5)» یا «(6400) 16»: داخل پرانتز سرعت/نسل رم است، نه ظرفیت
    if not s.strip():
        return None, False
    ambiguous = "+" in s
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*(tb|t|gb|g)?", s)
    vals = []
    for n, u in nums:
        n = float(n)
        if not u:
            u = "tb" if (unitless == "auto" and n <= 4) else "gb"
        vals.append(n * 1024 if u in ("tb", "t") else n)
    if not vals:
        return None, False
    return int(max(vals)), ambiguous


# ------------------------------------------------------------------ برند
BRANDS = [
    (r"asus|ایسوس", "Asus"), (r"lenovo|لنوو", "Lenovo"),
    (r"(?<![a-z])hp(?![a-z])|اچ ?پی", "HP"), (r"(?<![a-z])acer(?![a-z])|ایسر", "Acer"),
    (r"(?<![a-z])dell(?![a-z])|(?<!\S)دل(?!\S)", "Dell"),
    (r"(?<![a-z])msi(?![a-z])|ام ?اس ?آی", "MSI"), (r"apple|اپل|macbook", "Apple"),
    (r"samsung|سامسونگ", "Samsung"), (r"xiaomi|شیائومی|redmi", "Xiaomi"),
    (r"huawei|هواوی", "Huawei"), (r"honor|آنر", "Honor"),
    (r"logitech|لاجیتک", "Logitech"), (r"a4 ?tech|ای ?فورتک", "A4Tech"),
    (r"razer|ریزر", "Razer"), (r"redragon|ردراگون", "Redragon"),
    (r"rapoo|رپو", "Rapoo"), (r"havit|هویت", "Havit"), (r"baseus|بیسوس", "Baseus"),
    (r"anker|انکر", "Anker"), (r"gigabyte|گیگابایت", "Gigabyte"),
    (r"asrock|اس ?راک", "ASRock"), (r"intel|اینتل", "Intel"),
    (r"(?<![a-z])amd(?![a-z])|ای ?ام ?دی", "AMD"),
    (r"tp-?link|تی ?پی ?لینک", "TP-Link"), (r"d-?link|دی ?لینک", "D-Link"),
    (r"tenda|تندا", "Tenda"), (r"mikrotik|میکروتیک", "MikroTik"),
    (r"hikvision|هایک ?ویژن", "Hikvision"), (r"canon|کنون|کانن", "Canon"),
    (r"epson|اپسون", "Epson"), (r"brother|برادر", "Brother"),
    (r"kingston|کینگستون", "Kingston"), (r"western digital|(?<![a-z])wd(?![a-z])|وسترن", "WD"),
    (r"seagate|سیگیت", "Seagate"), (r"adata|ای ?دیتا", "ADATA"),
    (r"lexar|لکسار", "Lexar"), (r"sandisk|سن ?دیسک", "SanDisk"),
    (r"zotac|زوتک", "Zotac"), (r"jbl", "JBL"), (r"sony|سونی", "Sony"),
    (r"edifier|ادیفایر", "Edifier"), (r"verbatim|وربیتیم", "Verbatim"),
]
BRAND_RE = [(re.compile(p, re.I), b) for p, b in BRANDS]


def detect_brand(name: str) -> str:
    s = norm(name).lower()
    for rx, b in BRAND_RE:
        if rx.search(s):
            return b
    return ""


# ------------------------------------------------------------------ دسته
def split_categories(cat: str):
    paths = [[p.strip() for p in c.split(">")] for c in str(cat or "").split(",") if c.strip()]
    return paths


def product_group_and_leaf(cat: str):
    paths = split_categories(cat)
    if not paths:
        return "بدون دسته", ""
    flat = " | ".join(" > ".join(p) for p in paths)
    # لپ‌تاپ: دسته‌ای که با «لپ تاپ» شروع شود (نه «لوازم جانبی لپ تاپ»)
    if any(p[0] == "لپ تاپ" for p in paths):
        return "لپ‌تاپ", "لپ تاپ"
    paths = [p for p in paths if p[0] != "دسته بندی نشده"] or paths
    deepest = max(paths, key=len)
    leaf = deepest[-1]
    top = deepest[0]
    if "قطعات" in flat:
        return "قطعات کامپیوتر", leaf
    return top, leaf


# ---------------------------------------------------------- مشخصات لپ‌تاپ
CPU_PATTERNS = [
    r"ultra\s*[3579][\s-]*\d{3}[a-z]{0,2}",
    r"core\s*[3579][\s-]*\(?\d{3}[a-z]{0,2}\)?",
    r"i[3579][\s-]*\(?\d{4,5}[a-z]{0,2}\)?",
    r"ryzen\s*[3579]\s*(?:ai\s*)?(?:pro\s*)?[a-z]{0,2}\s*\d{3,4}[a-z]{0,2}",
    r"(?<![a-z])r[3579]\s*\d{4}[a-z]{0,2}",
    r"ai\s*[3579]\s*(?:max\+?\s*)?[a-z]{0,2}\s*\d{3}",
    r"celeron\s*[a-z]?\d{3,4}",
    r"(?<![a-z])n\d{4}(?![a-z0-9])",
    r"athlon\s*\w*",
    r"(?<![a-z])i[3579](?![a-z0-9])",
    r"ryzen\s*[3579]",
    # پشتیبان‌ها: آخرین راه‌حل وقتی الگوهای بالا چیزی پیدا نکنند
    r"core\s*ultra\s*[3579]",
    r"cori?e\s*i[3579][\s-]*\d{4}[a-z]{0,2}",
    r"(?<![a-z])r[3579]\s*\d{3}(?!\d)",
    r"(?<![a-z0-9])\d{5}h[xsk]?(?![a-z0-9])",
]
CPU_RE = [re.compile(p, re.I) for p in CPU_PATTERNS]
GPU_DED_RE = re.compile(
    r"rtx|gtx|geforce|nvidia|quadro|gddr|(?<![a-z])rx\s*\d{3,4}|(?<![a-z])mx\s*\d{3}|کارت گرافیک اختصاصی|گرافیک اختصاصی",
    re.I)
GPU_INT_RE = re.compile(
    r"integrated|یکپارچه|مجتمع|اشتراکی|shared|onboard|(?:intel\s*)?uhd(?:\s*graphics)?|(?:intel\s*)?iris(?:\s*xe)?|"
    r"intel graphics|intel hd|arc graphics|radeon graphics|"
    r"radeon\s*\d{3}m|vega\s*\d|(?<![a-z])fhd-vega",
    re.I)
GPU_DEDICATED = re.compile(
    r"rtx\s*\d{4}(?:\s*ti|\s*super)?|gtx\s*\d{4}|(?<![a-z])rx\s*\d{4}\w*|(?<![a-z])mx\s*\d{3}", re.I)
GPU_INTEGRATED = re.compile(r"radeon\s*\d{3}m|vega\s*\d|iris\s*xe|uhd|intel\s*arc|(?<![a-z])arc(?![a-z])", re.I)
GAMING_WORDS = re.compile(r"gaming|گیمینگ|tuf|rog|legion|loq|nitro|predator|victus|omen|katana|thin gf|cyborg|raider|stealth", re.I)
RAM_TYPE_RE = re.compile(r"(lpddr[45]x?|ddr[345])", re.I)


def parse_cpu(s: str):
    s = s.lower()
    for rx in CPU_RE:
        ms = list(rx.finditer(s))
        if not ms:
            continue
        # کد مدل لپ‌تاپ (مثل I38128) شبیه CPU است؛ مچی را ترجیح بده که پسوند حرفی دارد (1315U, 13700H)
        pick = next((m for m in ms if re.search(r"\d[a-z]{1,2}\)?$", m.group(0).strip())), ms[0])
        out = re.sub(r"[()]", " ", pick.group(0))
        return re.sub(r"\s+", " ", out).upper().strip()
    return ""


def intel_gen(cpu: str):
    m = re.search(r"I[3579][\s-]*(\d{4,5})", cpu)
    if not m:
        return None
    d = m.group(1)
    if len(d) == 5:
        return int(d[:2])
    return int(d[:2]) if d.startswith("1") else int(d[0])


def parse_storage_ram_from_text(s: str):
    """از اسم محصول: (ram_gb, ssd_gb, flags)"""
    s = norm(s).lower()
    flags = []
    ssd = ram = None
    spans = []
    # SSD: «512GB SSD» ، «1TB SSD» ، «512SSD» ، «1SSD»
    m = re.search(r"(\d+)\s*(gb|g|tb|t)?\s*(?:nvme\s*)?ssd", s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if not u:
            u = "tb" if n <= 4 else "gb"
        ssd = n * 1024 if u in ("tb", "t") else n
        spans.append(m.span())
    masked = list(s)
    for a, b in spans:
        for i in range(a, b):
            masked[i] = " "
    ms = "".join(masked)
    # RAM: «16GB DDR5» یا «DDR5 16GB» یا «16G RAM»
    m = re.search(r"(\d+)\s*(?:gb|g)\s*(?:lp)?(?:ddr|ram)", ms) or re.search(r"ddr\d\w*\s*(\d+)\s*(?:gb|g)\b", ms)
    if m:
        ram = int(m.group(1))
        for i in range(*m.span()):
            masked[i] = " "
        ms = "".join(masked)
    # باقی‌مانده‌ها
    left = [(int(n), u) for n, u in re.findall(r"(\d{1,4})\s*(gb|g|tb|t)\b", ms)]
    for n, u in left:
        gb = n * 1024 if u in ("tb", "t") else n
        if ram is None and u in ("gb", "g") and n in (4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96):
            ram = n
        elif ssd is None and (u in ("tb", "t") or gb >= 128):
            ssd = gb
    return ram, ssd, flags


def parse_screen(*texts):
    for t in texts:
        m = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:اینچ|inch|\")", norm(t), re.I)
        if m:
            v = float(m.group(1))
            if 10 <= v <= 18.5:
                return v
    return None


def parse_weight(desc: str):
    m = re.search(r"وزن[^\d\n]{0,25}(\d(?:\.\d{1,2})?)\s*(?:کیلو|kg)", norm(desc), re.I)
    return float(m.group(1)) if m else None


# ------------------------------------------------ مشخصات مادربرد / لوازم
CHIPSET_SOCKET = {
    "H410": "LGA1200", "B460": "LGA1200", "H510": "LGA1200", "B560": "LGA1200", "Z490": "LGA1200", "Z590": "LGA1200",
    "H610": "LGA1700", "B660": "LGA1700", "B760": "LGA1700", "Z690": "LGA1700", "Z790": "LGA1700", "H670": "LGA1700",
    "A320": "AM4", "B450": "AM4", "B550": "AM4", "X570": "AM4", "A520": "AM4",
    "A620": "AM5", "B650": "AM5", "X670": "AM5", "B850": "AM5", "X870": "AM5",
}


def parse_board(name: str, desc: str):
    s = norm(name + " " + desc).upper()
    chip = ""
    m = re.search(r"(?<![A-Z0-9])([HBZQ]\d{3}|[ABX]\d{3}[A-Z]?)(?![0-9])", s)
    if m:
        chip = m.group(1)[:4]
    sock = ""
    m = re.search(r"LGA\s?-?(\d{4})", s)
    if m:
        sock = "LGA" + m.group(1)
    elif re.search(r"(?<![A-Z])AM[45](?![0-9])", s):
        sock = re.search(r"(?<![A-Z])(AM[45])(?![0-9])", s).group(1)
    elif chip in CHIPSET_SOCKET:
        sock = CHIPSET_SOCKET[chip]
    rt = ""
    m = re.search(r"(?<![A-Z0-9])(DDR[345])(?![0-9])", s) or re.search(r"(?<![A-Z0-9])D([345])(?![A-Z0-9])", s)
    if m:
        rt = m.group(1) if m.group(1).startswith("DDR") else "DDR" + m.group(1)
    ff = ""
    m = re.search(r"MICRO[\s-]?ATX|MATX|MINI[\s-]?ITX|(?<![A-Z])ATX", s)
    if m:
        ff = {"MATX": "mATX"}.get(m.group(0).replace(" ", "").replace("-", ""), m.group(0).title())
    return chip, sock, rt, ff


def parse_compat_inch(text: str):
    s = norm(text)
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*(?:تا|-|–|to)\s*(\d{1,2}(?:\.\d)?)\s*(?:اینچ|inch)", s, re.I)
    if m:
        return float(m.group(2))
    m = re.search(r"(?:تا|حداکثر|up to)\s*(\d{1,2}(?:\.\d)?)\s*(?:اینچ|inch)", s, re.I)
    if m:
        return float(m.group(1))
    vals = [float(x) for x in re.findall(r"(\d{2}(?:\.\d)?)\s*(?:اینچ|inch)", s, re.I) if 10 <= float(x) <= 18.5]
    return max(vals) if vals else None


# ------------------------------------------------------------ نام ویژگی‌ها
ATTR_MAP = {
    "ram": {"ram", "رم", "رم (ram)", "حافظه رم"},
    "ssd": {"ssd", "sdd", "حافظه ssd", "حافظه داخلی", "نوع حافظه داخلی", "نوع حافظه", "حافظه"},
    "cpu": {"cpu", "پردازنده (cpu)", "پردازنده"},
    "cpu_gen": {"نسل پردازنده"},
    "gpu": {"gpu", "کارت گرافیک (gpu)", "نوع کارت گرافیک", "کارت گرافیک"},
    "screen": {"سایز صفحه نمایش", "سایز صفحه‌نمایش", "سایز صفحه نمایش", "سایز", "سایز صفحه"},
    "ram_type": {"نوع رم"},
    "color": {"رنگ"},
}
ATTR_LOOKUP = {norm(a).lower(): k for k, vs in ATTR_MAP.items() for a in vs}


def read_attrs(row):
    out = {}
    multi = []
    for i in range(1, MAX_ATTR + 1):
        n, v = row.get(f"نام ویژگی {i}"), row.get(f"مقدار ویژگی {i}")
        if pd.isna(n) or pd.isna(v):
            continue
        key = ATTR_LOOKUP.get(norm(n).lower().replace("‌", " "))
        if not key:
            continue
        v = str(v).strip()
        if "," in v:
            multi.append(key)
            continue
        out[key] = v
    return out, multi


# --------------------------------------------------------------- ساخت جدول
def build_table(df):
    """DataFrame با ستون‌های خروجی ووکامرس -> (جدول تمیز، شمارنده‌ی حذف‌شده‌ها)."""
    df = df.copy()
    df.columns = [str(c).replace("\ufeff", "") for c in df.columns]
    df["شناسه"] = df["شناسه"].astype(int)
    parents = df[df["نوع"].isin(["simple", "variable"])].set_index("شناسه", drop=False)
    pub_parents = set(parents[parents["منتشر شده"] == 1].index)
    variations = df[df["نوع"] == "variation"].copy()
    variations["pid"] = variations["مادر"].astype(str).str.replace("id:", "", regex=False).astype(int)

    # مجموعه RAM تنوع‌ها برای هر مادر (تشخیص اختلاف اسم با گزینه‌ها)
    var_rams = {}
    for _, v in variations.iterrows():
        a, _m = read_attrs(v)
        g, _a = to_gb(a.get("ram")) if "ram" in a else (None, False)
        if g:
            var_rams.setdefault(v["pid"], set()).add(g)

    rows = []
    dropped = {"unpublished_parent": 0, "unpublished_variation": 0}

    def build(sell, parent):
        pid = int(parent["شناسه"])
        name = norm(sell["نام"]) or norm(parent["نام"])
        parent_name = norm(parent["نام"])
        cat = parent["دسته‌ها"] if not pd.isna(parent["دسته‌ها"]) else ""
        group, leaf = product_group_and_leaf(cat)
        short = html_to_text(parent["توضیح کوتاه"])
        desc = html_to_text(parent["توضیحات"])
        text_all = f"{parent_name}\n{short}\n{desc}"
        flags = []

        attrs, multi = read_attrs(parent)
        if sell is not parent:
            a2, m2 = read_attrs(sell)
            attrs.update(a2)
        elif multi:
            flags.append("ویژگی چندمقداری روی محصول ساده")

        status = str(sell["در انبار؟"]) if not pd.isna(sell["در انبار؟"]) else ""
        in_stock = status == "1"
        rec = dict(
            id=int(sell["شناسه"]), parent_id=pid, row_type="variation" if sell is not parent else "simple",
            name=name, parent_name=parent_name, group=group, category_leaf=leaf,
            category_full=cat, brand=detect_brand(parent_name),
            stock_at_export="موجود" if in_stock else ("پیش‌سفارش" if status == "backorder" else "ناموجود"),
            availability_reason="موجود" if in_stock else "ناموجود موقت",
            color=attrs.get("color", ""),
        )

        if group == "لپ‌تاپ":
            nm = norm(parent_name).lower()
            ram_t, ssd_t, _ = parse_storage_ram_from_text(parent_name)
            ram, ssd = None, None
            if "ram" in attrs:
                ram, _ = to_gb(attrs["ram"])
            if "ssd" in attrs:
                ssd, amb = to_gb(attrs["ssd"], "auto")
                if amb:
                    flags.append("SSD شامل ‘+’ (احتمالاً eMMC+SSD)")
            # برای محصول ساده یا وقتی ویژگی نیست، از اسم
            if sell is parent:
                ram, ssd = ram or ram_t, ssd or ssd_t
            else:
                ram, ssd = ram or ram_t, ssd or ssd_t
            if sell is not parent and ram_t and parent["شناسه"] in var_rams and ram_t not in var_rams[parent["شناسه"]]:
                flags.append("RAM اسم با گزینه‌های تنوع نمی‌خواند")
            # --- پردازنده و نسل: فقط آنچه خودت نوشته‌ای (ویژگی سایت، بعد اسم/توضیحات)؛ هیچ حدسی نیست
            if attrs.get("cpu"):
                cpu, cpu_source = attrs["cpu"].strip(), "ویژگی سایت"
            else:
                cpu = parse_cpu(nm)
                cpu_source = "اسم محصول" if cpu else ""
            gen, gen_text, gen_source = None, "", ""
            if attrs.get("cpu_gen"):
                gen_text, gen_source = attrs["cpu_gen"].strip(), "ویژگی سایت"
                m = re.search(r"نسل\s*(\d{1,2})", norm(gen_text)) or re.fullmatch(r"\s*(\d{1,2})\s*", norm(gen_text))
                gen = int(m.group(1)) if m else None
            else:
                m = re.search(r"نسل\s*(\d{1,2})", norm(short + " " + desc))
                if m:
                    gen, gen_source = int(m.group(1)), "توضیحات"
            # --- گرافیک: اختصاصی (کارت گرافیک جدا) یا یکپارچه (Integrated)؛ ترتیب منبع: ویژگی سایت ← اسم ← توضیحات
            gpu, gpu_type, gpu_source = "", "", ""
            for src, txt in (("ویژگی سایت", attrs.get("gpu") or ""), ("اسم محصول", nm), ("توضیحات", norm(short + " " + desc))):
                if not txt:
                    continue
                if src == "ویژگی سایت" and re.fullmatch(r"[\d\s]*(?:گیگابایت|گیگ|gb)?\s*gddr\d*x?", norm(txt).lower().strip()):
                    continue  # فقط حافظه‌ی گرافیک نوشته شده، نه مدل کارت
                md = GPU_DED_RE.search(txt)
                mi = GPU_INT_RE.search(txt)
                if md or mi:
                    gpu_type = "dedicated" if md else "integrated"
                    gpu_source = src
                    if src == "ویژگی سایت":
                        gpu = txt.strip()
                    else:
                        mm = (GPU_DEDICATED.search(txt) if md else None) or md or mi
                        gpu = re.sub(r"(RTX|GTX|RX|MX)\s*(\d)", r"\1 \2", re.sub(r"\s+", " ", mm.group(0)).upper())
                    break
            if not gpu_type:
                # قاعده‌ی صاحب فروشگاه: اگر گرافیک جدا نوشته نشده، یعنی یکپارچه است
                gpu_type, gpu_source, gpu = "integrated", "پیش‌فرض (گرافیک جدا نوشته نشده)", "یکپارچه"
            m = None
            screen = None
            if attrs.get("screen"):
                screen = parse_screen(attrs["screen"] + " اینچ") or parse_screen(attrs["screen"])
            screen = screen or parse_screen(parent_name, short, desc)
            rt_attr = re.search(r"\((\d)\)", norm(attrs.get("ram", "")))
            rt = RAM_TYPE_RE.search(norm(attrs.get("ram_type", "")) + " " + nm + " " + norm(desc))
            ram_type = (f"DDR{rt_attr.group(1)}" if rt_attr and rt_attr.group(1) in "345"
                        else (rt.group(1).upper() if rt else ""))
            # نوع پنل: از اسم (با مرز دقیق، تا کد مدل مثل F1504VA اشتباه نشود) یا کنار کلمه «پنل» در توضیحات
            pn = r"(?<![a-z0-9])(oled|ips|tn|va)(?![a-z0-9])"
            panel = re.search(pn, nm) or re.search(r"پنل\s*(?:\S+\s+){0,2}?" + pn, norm(desc).lower())
            if panel and panel.re.pattern == pn:
                panel_val = panel.group(1)
            else:
                panel_val = panel.group(panel.lastindex) if panel else ""
            # قاعده‌ی کاربرد (از طرف صاحب فروشگاه): گرافیک اختصاصی ← گیمینگ + برنامه‌نویسی/کار سنگین؛
            # گرافیک یکپارچه (اشتراکی با رم) ← فقط کارهای روزمره
            if gpu_type == "dedicated" and re.search(r"(?<![a-z])mx\s*\d{3}", gpu.lower()):
                is_gaming, suitable = False, "کارهای روزمره؛ گرافیک سبک (MX)"
            elif gpu_type == "dedicated":
                is_gaming, suitable = True, "گیمینگ؛ برنامه‌نویسی و کارهای سنگین"
            elif gpu_type == "integrated":
                is_gaming, suitable = False, "کارهای روزمره"
            else:
                is_gaming, suitable = None, ""
                flags.append("گرافیک (اختصاصی/یکپارچه) نوشته نشده")
            rec.update(
                cpu=cpu, cpu_source=cpu_source, cpu_gen=gen, cpu_gen_text=gen_text, cpu_gen_source=gen_source,
                ram_gb=ram, ram_type=ram_type, ssd_gb=ssd,
                gpu=gpu, gpu_type=gpu_type, gpu_source=gpu_source, is_gaming=is_gaming, suitable_for=suitable,
                screen_inch=screen, weight_kg=parse_weight(text_all), panel=panel_val.upper(),
            )
            for fld, label in (("cpu", "پردازنده"), ("ram_gb", "RAM"), ("ssd_gb", "SSD"), ("screen_inch", "سایز صفحه")):
                if not rec.get(fld):
                    flags.append(f"{label} پیدا نشد")
            if rec.get("ram_gb") and rec["ram_gb"] > 128:
                rec["ram_gb"] = ram_t if (ram_t and ram_t <= 128) else None
                flags.append("RAM نامعقول؛ دستی چک شود")
            if rec.get("ram_gb") and rec["ram_gb"] not in (4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128):
                flags.append(f"RAM نامعمول ({rec['ram_gb']})")
            if rec.get("ssd_gb") and rec["ssd_gb"] not in (64, 128, 256, 512, 1024, 2048, 4096):
                flags.append(f"SSD نامعمول ({rec['ssd_gb']})")
        elif group == "قطعات کامپیوتر" or "مادربرد" in leaf:
            chip, sock, rt, ff = parse_board(parent_name, desc)
            rec.update(chipset=chip, socket=sock, ram_type=rt, form_factor=ff)
            if "مادربرد" in leaf:
                for fld, label in (("socket", "سوکت"), ("ram_type", "نوع رم")):
                    if not rec.get(fld):
                        flags.append(f"{label} پیدا نشد")
        elif re.search(r"کیف|کول ?پد|استند|پایه|هولدر", leaf + " " + norm(cat)) and "موبایل" not in norm(cat):
            c = parse_compat_inch(text_all)
            rec.update(compat_max_inch=c)
        if not rec["brand"] and (group == "لپ‌تاپ" or "مادربرد" in leaf):
            flags.append("برند شناخته نشد")  # لوازمِ بی‌برند (مثل پایه‌های ارگو) مشکلی نیست
        if not short and not desc:
            flags.append("بدون توضیحات")
        if group == "بدون دسته" or "دسته بندی نشده" in str(cat):
            flags.append("بدون دسته‌بندی")
        rec["review_flags"] = " ؛ ".join(dict.fromkeys(flags))
        rec["short_description"] = short
        rec["description_clean"] = desc
        return rec

    for pid, p in parents.iterrows():
        if pid not in pub_parents:
            dropped["unpublished_parent"] += 1
            continue
        if p["نوع"] == "simple":
            rows.append(build(p, p))
    for _, v in variations.iterrows():
        if v["pid"] not in pub_parents:
            dropped["unpublished_variation"] += 1
            continue
        rows.append(build(v, parents.loc[v["pid"]]))

    res = pd.DataFrame(rows)
    first = ["id", "parent_id", "row_type", "name", "group", "category_leaf", "brand", "stock_at_export",
             "availability_reason", "color", "cpu", "cpu_source", "cpu_gen", "cpu_gen_text", "cpu_gen_source",
             "ram_gb", "ram_type", "ssd_gb", "gpu", "gpu_type", "gpu_source", "is_gaming", "suitable_for",
             "screen_inch", "weight_kg", "panel", "chipset", "socket", "form_factor", "compat_max_inch",
             "review_flags", "parent_name", "category_full", "short_description", "description_clean"]
    res = res[[c for c in first if c in res.columns]]
    return res, dropped


def main(csv_path: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    res, dropped = build_table(df)
    res.to_csv(out / "products_clean.csv", index=False, encoding="utf-8-sig")

    review = res[res["review_flags"] != ""]
    with pd.ExcelWriter(out / "products_clean.xlsx", engine="openpyxl") as xw:
        res.to_excel(xw, "all_products", index=False)
        review.to_excel(xw, "needs_review", index=False)
        readme = pd.DataFrame({"توضیح": [
            "هر ردیف = یک محصول ساده یا یک تنوع (variation).",
            "ستون review_flags: دلیل مشکوک بودن. ردیف‌های زرد را چک کن و درست کن.",
            "availability_reason: ‘موجود’ / ‘ناموجود موقت’ / ‘متوقف شده’. ناموجودها به‌صورت پیش‌فرض ‘ناموجود موقت’ هستند؛ محصولات متوقف‌شده را خودت عوض کن.",
            "stock_at_export فقط مرجع است (وضعیت در زمان خروجی). موجودی و قیمت واقعی را زنده از ووکامرس بخوان.",
            "ssd_gb: ۱ ترابایت = ۱۰۲۴.",
            "cpu / cpu_gen / gpu فقط از چیزی خوانده شده که خودت نوشته‌ای (ستون *_source نشان می‌دهد از کجا: ویژگی سایت، اسم، یا توضیحات). هیچ نسلی از روی مدل حدس زده نشده.",
            "gpu_type: dedicated = کارت گرافیک جدا؛ integrated = گرافیک یکپارچه. اگر هیچ‌جا نوشته نشده باشد خالی می‌ماند و ردیف زرد می‌شود.",
            "is_gaming و suitable_for طبق قاعده‌ی خودت: گرافیک اختصاصی ← گیمینگ + برنامه‌نویسی/کار سنگین؛ گرافیک یکپارچه ← کارهای روزمره.",
        ]})
        readme.to_excel(xw, "README", index=False)
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
        yellow = PatternFill("solid", fgColor="FFF2CC")
        for sheet in ("all_products", "needs_review"):
            ws = xw.sheets[sheet]
            ws.freeze_panes = "E2"
            ws.auto_filter.ref = ws.dimensions
            for c in ws[1]:
                c.font = Font(bold=True)
            flag_col = list(res.columns).index("review_flags") + 1
            for r in range(2, ws.max_row + 1):
                if ws.cell(r, flag_col).value:
                    for c in range(1, ws.max_column + 1):
                        ws.cell(r, c).fill = yellow
            for i, col in enumerate(res.columns, 1):
                w = 60 if col in ("name", "parent_name", "review_flags") else (14 if col not in ("description_clean", "short_description", "category_full") else 40)
                ws.column_dimensions[get_column_letter(i)].width = w

    print(f"ردیف‌های خروجی: {len(res)}  |  نیاز به بررسی: {len(review)}  |  حذف‌شده: {dropped}")
    print(res["group"].value_counts().to_string())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("استفاده: python clean_products.py <فایل CSV> <پوشه خروجی>")
    main(sys.argv[1], sys.argv[2])
