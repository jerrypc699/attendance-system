# Anti-Cheat Biometric Attendance System v2.0 🛡️
### نظام حضور وانصراف ذكي للشركات (Android + iPhone PWA + Windows Server)

---

## المميزات الأمنية المفعّلة في النسخة 2.0 (Production-Ready)

1. **توثيق إداري كامل (JWT + bcrypt):**
   - لوحة التحكم (`/dashboard`) وجميع مسارات `/api/v1/admin/*` محمية بتسجيل دخول الأدمن وتغيير كلمة المرور.
   - تم إزالة أي تسريب لمتجهات الوجه أو قوائم الموظفين، وتطبيق Soft Delete للحفاظ على سجلات الرواتب والتدقيق.
2. **ذكاء اصطناعي حقيقي للوجه والحيوية على السيرفر (Server-Side AI):**
   - محرك **OpenCV YuNet** لكشف الوجه و 5 نقاط ملامح، ومحرك **SFace ONNX (128-d)** لمطابقة الهوية على السيرفر مباشرة (لا يثق أبداً في متجهات أو درجات حيوية مرسلة من الهاتف).
   - التقاط **3 إطارات متتالية** أثناء تنفيذ الحركة العشوائية لمنع الصور الثابتة وفحص الحيوية السلبي (حدة الصورة، الانعكاسات، مواريه الشاشات).
3. **حماية التواجد الفيزيائي للآيفون والأندرويد (Android & iPhone):**
   - **Geofencing GPS** حقيقي + كشف Fake GPS (`isMocked`) على أندرويد.
   - **IP واي فاي الشركة (Office Public IP Whitelist)** عبر ترويسة `CF-Connecting-IP` (يعمل على iPhone PWA و Android).
   - **شاشة QR متغير كل 20 ثانية (`/kiosk/{branch_code}`)** موقّع بـ HMAC لمدخل الشركة.
4. **ربط جهاز واحد لكل موظف + كود تفعيل من الـ HR:**
   - يضيف الأدمن الموظف فيظهر له **كود تفعيل أحادي الاستخدام (`ACT-XXXXXX`)** + رسالة جاهزة لواتساب.
   - عند التفعيل يحصل هاتف الموظف على مفتاح سري (`device_token`) محفوظ محلياً لمنع البصم بالنيابة أو انتحال الجهاز.
5. **تخزين احترافي وتقارير ونسخ احتياطي:**
   - حفظ صور البصمات كملفات JPEG مضغوطة في `uploads/snapshots/` محمية بتوثيق الأدمن بدلاً من تضخيم قاعدة البيانات.
   - تصدير تقارير الحضور بصيغة Excel CSV (بالعربية) من لوحة التحكم.
   - تشغيل دائم كـ Windows Service + نسخ احتياطي يومي ساخن لقاعدة البيانات والصور في `backups/`.

---

## التشغيل أونلاين بـ HTTPS من هذا السيرفر (Windows Server 2019)

### 1. الخدمات المثبّتة تلقائياً
- **`AttendanceSystem-API`:** تعمل في الخلفية 24/7 وتبدأ تلقائياً مع تشغيل السيرفر على المنفذ `8000`.
- **`AttendanceSystem-DailyBackup`:** تأخذ نسخة احتياطية يومية الساعة 2:00 صباحاً داخل `backups/`.
- للتثبيت أو إعادة التثبيت في أي وقت (كـ Administrator):
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_services.ps1
  ```

### 2. ربط السيرفر بدومين الشركة بـ HTTPS دائم (Cloudflare Tunnel — مجاني)
1. اربط دومين الشركة على حسابك المجاني في Cloudflare.
2. نفّذ الأمر التالي كـ Administrator:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\setup_cloudflare_tunnel.ps1 -DomainName attendance.yourcompany.com
   ```
3. حدّث `.env` بالقيم:
   ```env
   ALLOWED_ORIGINS=https://attendance.yourcompany.com
   PUBLIC_BASE_URL=https://attendance.yourcompany.com
   ```
   ثم أعد تشغيل المهمة `AttendanceSystem-API`.

### 3. تجربة فورية بـ HTTPS بدون دومين الآن (Quick Tunnel)
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_quick_tunnel.ps1
```
افتح الرابط الناتج `https://....trycloudflare.com/mobile` على iPhone أو Android لتجربة الكاميرا والـ GPS فوراً.

---

## توزيع التطبيق على الموظفين

- **لموظفي iPhone و Android (فوراً ومجاناً):**
  افتح رابط `https://attendance.yourcompany.com/mobile` ->
  - **iPhone (Safari):** اضغط مشاركة `⬆️` ثم **إضافة إلى الشاشة الرئيسية (Add to Home Screen)**.
  - **Android (Chrome):** اضغط زر **تثبيت** أو حمّل تطبيق Flutter APK.
- **بناء نسخة Android APK الرسمية تلقائياً:**
  من GitHub Actions -> شغّل Workflow **`Build Production Android APK & AAB`** وأدخل رابط الدومين `https://attendance.yourcompany.com` في `api_url` ليخرج ملف `app-release.apk` جاهزاً في **GitHub Releases**.

---

## تشغيل الاختبارات الآلية
```powershell
.\venv\Scripts\python -m pytest -v tests
```
