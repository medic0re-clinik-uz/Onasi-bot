# Onasi: fonda yashaydigan ovozli yordamchi

"Onasi" deb chaqirsangiz chiqadigan, savolingizga ovoz bilan javob beradigan,
rejalaringizni eslab qoladigan shaxsiy yordamchi. Hammasi bepul.

## Qanday ishlaydi
- Kompyuter bilan birga fonda ishga tushadi. Ekranda ko'rinmaydi, faqat soat yonida ikonka turadi.
- Mikrofonni doim tinglaydi, lekin **faqat "Onasi" so'zini** kutadi. Bu kompyuterning
  o'zida (oflayn, Vosk) ishlaydi, chaqirilmaguncha hech narsa internetga ketmaydi.
- Chaqirsangiz o'ng pastda chiroyli oyna chiqadi, savolingizni eshitadi va javob beradi.
- Javobdan keyin ~7 soniya yana tinglaydi, shuning uchun qayta "Onasi" demasdan davom ettirish mumkin.
- Gap tugagach "rahmat" yoki "xayr" desangiz yopiladi.

## Nimalar deyish mumkin
- "Onasi, ertaga uchun reja qo'sh: soat o'nda yig'ilish"  (saqlaydi)
- "Onasi, bugun nima qilamiz?"  (rejalarni o'qiydi, ob-havoga qarab maslahat beradi)
- "Onasi, bugun havo qanaqa?"
- "Onasi, ertangi rejalarni tozala"
- Istalgan umumiy savol (Gemini orqali)

Ikonkani bossangiz ham chaqiriladi (ism tanilmay qolsa).

## O'rnatish (Windows)
1. Fayllarni `C:\ertalab` papkasiga qo'ying (`onasi.py`, `requirements.txt`, `onasi.bat`, `onasi_sinov.bat`).
2. Terminalda papkada: `pip install -r requirements.txt`
   (`PySide6` katta, ~200 MB, biroz kuting). Google Calendar kerak bo'lmasa, oxirgi 3 qatorni
   `requirements.txt` dan olib tashlang.
3. `onasi.py` ichida `ISM` ga ismingizni yozing.
4. Gemini kaliti (oldingi qadamdagidek) `GEMINI_API_KEY` sifatida o'rnatilgan bo'lsin.
5. Birinchi sinov: `onasi_sinov.bat` ni ikki marta bosing. Birinchi marta ovoz modeli
   (~50 MB) o'zi yuklanadi, bir necha daqiqa ketishi mumkin. "Onasi tayyor" degan oyna chiqadi.
6. "Onasi" deng.

## Avtomatik ishga tushirish
`Win + R` > `shell:startup` > `onasi.bat` ni shu papkaga qo'ying.
Eski `ertalab.bat` ni Startup papkasidan olib tashlang, aks holda ikki yordamchi ishlaydi.

## Muammolar
- Hammasi `C:\ertalab\onasi.log` fayliga yoziladi. Xato bo'lsa, shu fayl oxirini yuboring.
- **"Onasi" deganda tanimasa:** `onasi.py` boshidagi `ISM_ANIQLIK = 0.8` ni `0.7` ga tushiring.
  Ko'p xato ishga tushsa, `0.85` ga ko'taring.
- **Gapirayotganimni sezmasa / hech qachon to'xtamasa:** `MIN_CHEGARA = 450` ni
  `300` ga tushiring (sezgirroq) yoki `700` ga ko'taring (kamroq sezgir).
- Yopish: ikonkaga o'ng tugma > "Chiqish".
- Mikrofon ruxsati yoqilgan bo'lsin (Sozlamalar > Maxfiylik > Mikrofon).
