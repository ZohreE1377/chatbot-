# بک‌اند چت‌بات پلاس رایان

## اجرا (روی سرور یا کامپیوتر خودت)
```bash
cd backend
pip install -r requirements.txt
# ویندوز PowerShell:
$env:LLM_API_KEY="کلید-avalai"
$env:LLM_BASE_URL="https://api.avalai.ir/v1"
$env:LLM_MODEL="gpt-4o-mini"
uvicorn main:app --host 0.0.0.0 --port 8000
```
تست: `http://localhost:8000/health` باید `{"ok":true,...}` بدهد.

## وصل کردن به سایت
1. در `widget/plusrayan-chat.html` خط `PR_CHAT_API` را به آدرس بک‌اند (مثلاً `https://api.example.com/api/chat`) تغییر بده.
2. در WPCode یک HTML Snippet تازه بساز، کد را paste کن، Auto Insert ← **Site Wide Footer** ← Active.
3. اسنیپت چت قبلی را **Deactivate** کن تا دو دایره‌ی آبی نداشته باشی.

## نکته‌ها
- ngrok رایگان فقط برای تست است؛ برای سایت واقعی یک سرور همیشه‌روشن (VPS) با HTTPS بگیر.
- قیمت و موجودی هر بار زنده از `plusrayan.ir/wp-json/wc/store/v1/products` می‌آید (۵ دقیقه کش).
- فایل‌های `data/` را بعد از هر خروجی جدید ووکامرس و اجرای `clean_products.py` عوض کن.

## همگام‌سازی خودکار با ووکامرس (محصول جدید، حذف و تغییر مشخصات)
قیمت و موجودی همیشه زنده است. مشخصات (رم، گرافیک، …) را این بخش هر ۱۲ ساعت از ووکامرس می‌گیرد و دیتا را به‌روز می‌کند.

1. ووکامرس ← تنظیمات ← پیشرفته ← **REST API** ← افزودن کلید. دسترسی را **فقط خواندن (Read)** بگذار و کلید `ck_...` و رمز `cs_...` را کپی کن.
2. قبل از اجرای `uvicorn` تنظیم کن:
   ```
   $env:WC_KEY="ck_..."
   $env:WC_SECRET="cs_..."
   $env:ADMIN_TOKEN="یک-رمز-طولانی"   # برای اجرای دستی (اختیاری)
   ```
3. بعد از بالا آمدن سرور، تقریباً ۲۰ ثانیه بعد اولین همگام‌سازی انجام می‌شود. وضعیتش را در `/health` ببین (بخش `sync`: `last_ok`، `last_error`).
4. اجرای دستی بعد از افزودن محصول:
   `curl -X POST -H "X-Admin-Token: رمز" http://localhost:8000/admin/sync`
- اگر ووکامرس خطا بدهد یا تعداد لپ‌تاپ‌ها ناگهان کمتر از نصف شود، دیتای قبلی **دست‌نخورده** می‌ماند و خطا در `/health` و ترمینال ثبت می‌شود.
- پیش‌نویس‌ها و محصولات منتشرنشده وارد نمی‌شوند.

## به‌روز شدن فرم Finder
در WPCode، **بالای** کد فرم این خط را اضافه کن (آدرس بک‌اند خودت):
```html
<script>window.PR_FINDER_DATA="https://آدرس-بک‌اند/api/laptops";</script>
```
اگر بک‌اند در دسترس نباشد، فرم با دیتای داخل خودش کار می‌کند.
