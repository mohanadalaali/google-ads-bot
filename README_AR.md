# Google Ads Guard Bot - نسخة جاهزة

بوت مراقبة وتنبيه لحساب Google Ads. هدفه الإنذار المبكر وتقليل خطر التعليق، وليس التحايل على سياسات Google.

## ماذا يراقب؟
- الإعلانات المرفوضة DISAPPROVED
- الإعلانات المحدودة APPROVED_LIMITED
- الحملات المتوقفة أو المحذوفة
- ارتفاع التكلفة اليومي
- كلمات خطرة في نص الإعلان مثل: أرخص، مضمون 100%، بدون شروط

## التشغيل السريع

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
# أو Windows:
# venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

عدّل ملف `.env` وضع بياناتك، ثم شغّل:

```bash
python bot.py
```

## تشغيل كل ساعة على VPS

```bash
crontab -e
```

أضف:

```cron
0 * * * * cd /path/google_ads_guard_bot && /path/google_ads_guard_bot/venv/bin/python bot.py >> bot.log 2>&1
```

## ملاحظات مهمة
- لا تفعل `AUTO_PAUSE=true` إلا بعد اختبار النتائج.
- أول أسبوع شغّله تنبيهات فقط.
- البوت لا يمنع التعليق إذا كان الحساب مخالف، لكنه يساعدك تكتشف الخطر بدري.
