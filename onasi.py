"""
ONASI - fonda yashaydigan shaxsiy ovozli yordamchi (o'zbekcha, bepul).

- Kompyuter bilan birga ishga tushadi, ekranda ko'rinmaydi (faqat soat yonida ikonka).
- "Onasi" deb chaqirsangiz chiroyli oyna chiqadi, savolingizni eshitadi va ovoz bilan javob beradi.
- Rejalaringizni eslab qoladi, ob-havo va kalendardan foydalanadi.
- Ismni tanish kompyuterning o'zida (oflayn, Vosk) ishlaydi: chaqirilmaguncha hech narsa internetga ketmaydi.

Ishga tushirish:  python onasi.py      (yoki onasi.bat)
"""
import asyncio
import datetime as dt
import difflib
import hashlib
import json
import logging
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# ============================== SOZLAMALAR ==============================
ISM = "Samariddin"                    # <-- o'z ismingizni yozing
SHAHAR = "Toshkent"
LAT, LON = 41.3111, 69.2797
OVOZ = "uz-UZ-MadinaNeural"        # yoki "uz-UZ-SardorNeural" (erkak ovozi)
GEMINI_MODELLAR = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]   # nomi eskirsa, o'zi yangisini topadi

# Ismni tanish
ISM_SOZLAR = ["onasi", "онаси"]
ISM_ANIQLIK = 0.8                  # 0..1; past bo'lsa ko'proq tanadi (lekin xato ham ko'p)

# Mikrofon sezgirligi
MIN_CHEGARA = 450                  # gapirayotganingiz sezilmasa kamaytiring (masalan 300)
JIM_SONIYA = 0.9                   # shuncha jimlikdan keyin gapni tugagan deb hisoblaydi
                                   # (gap o'rtasida kesib qo'ysa 1.2 ga ko'taring)
MAX_SONIYA = 25                    # bitta savol uchun maksimal uzunlik

TUGATISH = ["xayr", "hayr", "to'xta", "toxta", "tugat", "bo'ldi", "boldi",
            "tamom", "rahmat", "stop", "хайр", "тугат", "бўлди", "тамом", "раҳмат"]

try:
    TZ = ZoneInfo("Asia/Tashkent")
except Exception:                  # Windows'da tzdata yo'q bo'lsa
    TZ = dt.timezone(dt.timedelta(hours=5))

BASE = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS = os.path.join(BASE, "credentials.json")
TOKEN = os.path.join(BASE, "token.json")
REJALAR_FAYL = os.path.join(BASE, "rejalar.json")
MODEL_PAPKA = os.path.join(BASE, "model")
KESH_PAPKA = os.path.join(BASE, "tts_kesh")        # qisqa jumlalar tayyor saqlanadi (tezroq)
MODEL_NOMI = "vosk-model-small-uz-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NOMI}.zip"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

HAFTA_KUNLARI = ["dushanba", "seshanba", "chorshanba", "payshanba",
                 "juma", "shanba", "yakshanba"]

logging.basicConfig(filename=os.path.join(BASE, "onasi.log"), level=logging.INFO,
                    format="%(asctime)s %(message)s", encoding="utf-8")


def log(*a):
    logging.info(" ".join(str(x) for x in a))


# ============================== YORDAMCHI FUNKSIYALAR ==============================
def hozir() -> dt.datetime:
    return dt.datetime.now(TZ)


def bugun_kaliti() -> str:
    return hozir().date().isoformat()


def ism_bormi(matn: str) -> bool:
    sozlar = matn.lower().replace("'", "").replace("`", "").split()
    nomzodlar = sozlar + [a + b for a, b in zip(sozlar, sozlar[1:])]
    for c in nomzodlar:
        for w in ISM_SOZLAR:
            if difflib.SequenceMatcher(None, c, w).ratio() >= ISM_ANIQLIK:
                return True
    return False


def ismni_olib_tashla(matn: str) -> str:
    s = matn.split()
    if s and ism_bormi(s[0]):
        s = s[1:]
    elif len(s) > 1 and ism_bormi(s[0] + s[1]):
        s = s[2:]
    return " ".join(s).strip(" ,.!?")


def qisqa_buyruq(matn: str, sozlar: list) -> bool:
    m = matn.lower()
    return len(m.split()) <= 4 and any(s in m for s in sozlar)


# ============================== REJALAR XOTIRASI ==============================
def _yukla() -> dict:
    try:
        with open(REJALAR_FAYL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _saqla(d: dict):
    chegara = (hozir().date() - dt.timedelta(days=30)).isoformat()
    d = {k: v for k, v in d.items() if k >= chegara}
    with open(REJALAR_FAYL, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def rejalar_ol(sana: str) -> list:
    return _yukla().get(sana, [])


def reja_qosh(sana: str, matn: str):
    d = _yukla()
    d.setdefault(sana, []).append(matn)
    _saqla(d)


def rejalar_tozala(sana: str):
    d = _yukla()
    d.pop(sana, None)
    _saqla(d)


# ============================== OB-HAVO ==============================
KOD = {
    0: "ochiq osmon", 1: "asosan ochiq", 2: "qisman bulutli", 3: "bulutli",
    45: "tumanli", 48: "tumanli", 51: "yengil shivalab yog'adigan yomg'ir",
    53: "shivalab yomg'ir", 55: "kuchli shivalab yomg'ir",
    61: "yengil yomg'ir", 63: "yomg'ir", 65: "kuchli yomg'ir",
    71: "yengil qor", 73: "qor", 75: "kuchli qor",
    80: "jala", 81: "kuchli jala", 82: "juda kuchli jala",
    95: "momaqaldiroq", 96: "do'l bilan momaqaldiroq", 99: "do'l bilan momaqaldiroq",
}


def ob_havo() -> str:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m"
        "&daily=weathercode,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max"
        "&timezone=Asia%2FTashkent&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r)
    hozirgi = round(d["current"]["temperature_2m"])
    mx = round(d["daily"]["temperature_2m_max"][0])
    mn = round(d["daily"]["temperature_2m_min"][0])
    yomgir = d["daily"]["precipitation_probability_max"][0]
    holat = KOD.get(d["daily"]["weathercode"][0], "o'zgaruvchan")
    matn = (f"{SHAHAR}da hozir {hozirgi} daraja. Bugun {holat}, "
            f"harorat {mn} dan {mx} darajagacha.")
    if yomgir is not None and yomgir >= 40:
        matn += f" Yog'ingarchilik ehtimoli {yomgir} foiz."
    return matn


# ============================== GOOGLE CALENDAR (ixtiyoriy) ==============================
def calendar_vazifalari():
    if not os.path.exists(CREDENTIALS) and not os.path.exists(TOKEN):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())

    servis = build("calendar", "v3", credentials=creds)
    boshi = hozir().replace(hour=0, minute=0, second=0, microsecond=0)
    oxiri = boshi + dt.timedelta(days=1)
    voqealar = servis.events().list(
        calendarId="primary", timeMin=boshi.isoformat(), timeMax=oxiri.isoformat(),
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    if not voqealar:
        return "Kalendaringizda bugun tadbir yo'q."
    qismlar = [f"Kalendaringizda {len(voqealar)} ta tadbir bor."]
    for v in voqealar:
        nom = v.get("summary", "Nomsiz tadbir")
        b = v["start"].get("dateTime")
        if b:
            t = dt.datetime.fromisoformat(b).astimezone(TZ)
            qismlar.append(f"Soat {t.strftime('%H:%M')} da: {nom}.")
        else:
            qismlar.append(f"Kun bo'yi: {nom}.")
    return " ".join(qismlar)


# ============================== GEMINI (bepul) + VOSITALAR ==============================
def gemini_bor() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


_SANA = {"type": "STRING", "description": "Sana YYYY-MM-DD ko'rinishida"}
VOSITALAR = [{"functionDeclarations": [
    {"name": "rejalarni_ol",
     "description": "Foydalanuvchining saqlangan rejalarini (qilinadigan ishlarini) berilgan sana uchun oladi.",
     "parameters": {"type": "OBJECT", "properties": {"sana": _SANA}, "required": ["sana"]}},
    {"name": "reja_qosh",
     "description": "Foydalanuvchining rejasiga yangi ish qo'shadi va saqlaydi.",
     "parameters": {"type": "OBJECT",
                    "properties": {"sana": _SANA,
                                   "matn": {"type": "STRING", "description": "Ishning qisqa tavsifi"}},
                    "required": ["sana", "matn"]}},
    {"name": "rejalarni_tozala",
     "description": "Berilgan sanadagi barcha saqlangan rejalarni o'chiradi.",
     "parameters": {"type": "OBJECT", "properties": {"sana": _SANA}, "required": ["sana"]}},
    {"name": "ob_havo",
     "description": f"{SHAHAR} uchun hozirgi va bugungi ob-havo ma'lumoti."},
    {"name": "kalendar",
     "description": "Bugungi Google Calendar tadbirlari."},
]}]


def tizim_matni() -> str:
    n = hozir()
    return (
        f"Sen «Onasi» ismli, {ISM}ning shaxsiy ovozli yordamchisisan. Har doim o'zbek tilida "
        "(lotin yozuvida) javob ber. Javoblaring ovoz bilan o'qiladi: juda qisqa (odatda 1-2 gap), oddiy "
        "so'zlashuv tilida bo'lsin; markdown, ro'yxat belgilari, yulduzcha, emoji ishlatma. "
        "Bilmaganingni o'ylab topma, bilmasang shuni ayt.\n"
        f"Hozir: {n.strftime('%Y-%m-%d %H:%M')}, {HAFTA_KUNLARI[n.weekday()]} ({SHAHAR} vaqti).\n"
        "Foydalanuvchi rejalari, qilinadigan ishlari haqida so'rasa yoki yangi ish aytsa, "
        "vositalardan foydalan (sanani YYYY-MM-DD ko'rinishida ber; kun aytilmasa bugun deb hisobla, "
        "'ertaga' desa ertangi sanani hisobla). Reja qo'shgach qisqa tasdiqla. "
        "Bugungi maslahat so'ralsa rejalar va ob-havoni olib, amaliy maslahat ber."
    )


def mavjud_modellar() -> list:
    """Hisobingiz uchun ishlaydigan 'flash' modellarini Google'dan so'raydi.
    Model nomlari eskirib, 404 chiqqanda avtomatik ishlatiladi."""
    kalit = os.environ["GEMINI_API_KEY"]
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": kalit})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    nomlar = set()
    for m in d.get("models", []):
        n = m.get("name", "").split("/")[-1]
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if "flash" not in n:
            continue
        if any(x in n for x in ("image", "tts", "live", "audio", "embedding", "thinking",
                                "exp", "robotics", "computer", "native")):
            continue
        nomlar.add(n)

    def tartib(n):
        v = re.findall(r"\d+(?:\.\d+)?", n)
        ver = float(v[0]) if v else 0.0
        return ("preview" in n, -ver, "lite" in n, n)   # barqaror, yangi, to'liq model oldin
    return sorted(nomlar, key=tartib)


def _chaqir(model: str, govda: dict, on_text=None):
    """(content | None, http_kod, xato_matni).
    Javobni oqim (stream) bilan oladi: matn bo'laklari kelishi bilan on_text ga beriladi,
    shunda butun javob tayyor bo'lishini kutmasdan gapira boshlash mumkin."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           ":streamGenerateContent?alt=sse")
    req = urllib.request.Request(
        url, data=json.dumps(govda).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    qismlar = []
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            for raw in r:
                satr = raw.decode("utf-8", "replace").strip()
                if not satr.startswith("data:"):
                    continue
                try:
                    d = json.loads(satr[5:])
                except ValueError:
                    continue
                nomzod = d.get("candidates") or []
                if not nomzod:
                    continue
                for p in (nomzod[0].get("content") or {}).get("parts", []):
                    if p.get("thought"):
                        continue
                    if "functionCall" in p:
                        qismlar.append(p)
                    elif "text" in p:
                        if qismlar and "text" in qismlar[-1] and "functionCall" not in qismlar[-1]:
                            qismlar[-1]["text"] += p["text"]
                        else:
                            qismlar.append(dict(p))
                        if on_text:
                            on_text(p["text"])
    except urllib.error.HTTPError as e:
        return None, e.code, e.read()[:400].decode("utf-8", "replace")
    except Exception as e:
        return None, 0, str(e)
    if not qismlar:
        return None, 200, "bo'sh javob"
    return {"role": "model", "parts": qismlar}, 200, ""


# "O'ylash" rejimini o'chirish usullari: yangi modellar thinkingLevel, eskilari thinkingBudget
# qabul qiladi. Qaysi biri ishlasa, shuni eslab qolamiz (o'ylash javobni sekinlashtiradi).
THINK_VARIANTLAR = [{"thinkingLevel": "minimal"}, {"thinkingBudget": 0}, None]
_THINK_I = 0


def jumlalarga_bol(buf: str, oxirigacha: bool = False, min_uzunlik: int = 25):
    """Matndan tugallangan jumlalarni ajratadi: (jumlalar, qoldiq).
    Belgidan keyin bo'sh joy kelsagina jumla tugagan hisoblanadi ('10.30' buzilmaydi).
    Juda qisqa jumlalar keyingisi bilan birlashtiriladi (ovoz uzilib qolmasligi uchun)."""
    jumlalar, boshi = [], 0
    for m in re.finditer(r"[.!?…]+\s+", buf):
        parcha = buf[boshi:m.end()].strip()
        if len(parcha) >= min_uzunlik:
            jumlalar.append(parcha)
            boshi = m.end()
    qoldiq = buf[boshi:]
    if oxirigacha and qoldiq.strip():
        jumlalar.append(qoldiq.strip())
        qoldiq = ""
    return jumlalar, qoldiq


def gemini_so_rov(contents: list, on_text=None) -> dict:
    global _THINK_I
    asos = {
        "systemInstruction": {"parts": [{"text": tizim_matni()}]},
        "contents": contents,
        "tools": VOSITALAR,
    }
    nomzodlar = list(GEMINI_MODELLAR)
    sinalgan, qidirildi, xato = set(), False, ""
    while nomzodlar:
        model = nomzodlar.pop(0)
        if model in sinalgan:
            continue
        sinalgan.add(model)
        content, kod, matn = None, 0, ""
        for vi in range(_THINK_I, len(THINK_VARIANTLAR)):
            govda = dict(asos)
            govda["generationConfig"] = {"maxOutputTokens": 1024, "temperature": 0.7}
            if THINK_VARIANTLAR[vi]:
                govda["generationConfig"]["thinkingConfig"] = THINK_VARIANTLAR[vi]
            content, kod, matn = _chaqir(model, govda, on_text)
            if content is None and kod == 400 and "think" in matn.lower():
                continue                       # bu usul qabul qilinmadi: keyingisini sinaymiz
            if content is not None and vi != _THINK_I:
                _THINK_I = vi
                log("Ishlaydigan 'o'ylash' sozlamasi:", THINK_VARIANTLAR[vi])
            break
        if content is not None:
            if GEMINI_MODELLAR[0] != model:
                GEMINI_MODELLAR.insert(0, model)       # ishlagan modelni eslab qolamiz
                log("Ishlaydigan model:", model)
            return content
        xato = f"{model}: HTTP {kod} {matn[:250]}"
        log("Gemini xatosi:", xato)
        if kod in (404, 403) and not qidirildi:
            qidirildi = True
            try:
                topilgan = mavjud_modellar()
                log("Topilgan modellar:", ", ".join(topilgan[:8]))
                nomzodlar += topilgan[:4]
            except Exception as e:
                log("Modellar ro'yxatini olib bo'lmadi:", e)
    raise RuntimeError(xato)


def _sana_tekshir(s) -> str:
    try:
        return dt.date.fromisoformat(str(s)).isoformat()
    except Exception:
        return bugun_kaliti()


def vosita_bajar(nom: str, args: dict):
    try:
        if nom == "rejalarni_ol":
            s = _sana_tekshir(args.get("sana"))
            return {"sana": s, "rejalar": rejalar_ol(s)}
        if nom == "reja_qosh":
            s = _sana_tekshir(args.get("sana"))
            reja_qosh(s, str(args.get("matn", "")).strip())
            return {"holat": "saqlandi", "sana": s}
        if nom == "rejalarni_tozala":
            s = _sana_tekshir(args.get("sana"))
            rejalar_tozala(s)
            return {"holat": "ochirildi", "sana": s}
        if nom == "ob_havo":
            return {"matn": ob_havo()}
        if nom == "kalendar":
            return {"matn": calendar_vazifalari() or "Kalendar ulanmagan."}
        return {"xato": f"noma'lum vosita: {nom}"}
    except Exception as e:
        log("Vosita xatosi:", nom, e)
        return {"xato": str(e)}


def tarixni_kes(tarix: list, maks: int = 24) -> list:
    """Eski xabarlarni olib tashlaydi, lekin vosita chaqiruvi/javobi juftligini buzmaydi."""
    while len(tarix) > maks:
        tarix.pop(0)
        while tarix and not (tarix[0]["role"] == "user" and
                             any("text" in p for p in tarix[0]["parts"])):
            tarix.pop(0)
    return tarix


# ============================== OVOZNI MATNGA (Google, bepul) ==============================
def ovozni_matnga(raw: bytes):
    """Matn | "" (tushunilmadi) | None (tarmoq xatosi)"""
    import speech_recognition as sr
    r = sr.Recognizer()
    audio = sr.AudioData(raw, 16000, 2)
    try:
        return r.recognize_google(audio, language="uz-UZ")
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        log("STT xatosi:", e)
        return None


async def _tts(matn: str, fayl: str):
    import edge_tts
    await edge_tts.Communicate(matn, OVOZ).save(fayl)


# ============================== ISHCHI (audio + miya) ==============================
from PySide6.QtCore import (Qt, QObject, Signal, QTimer, QRectF, QPointF,  # noqa: E402
                            QLockFile)
from PySide6.QtGui import (QPainter, QColor, QLinearGradient, QRadialGradient,  # noqa: E402
                           QPainterPath, QFont, QFontMetrics, QPen, QIcon,
                           QPixmap, QGuiApplication, QAction)
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu  # noqa: E402


class Signallar(QObject):
    holat = Signal(str)       # idle | xabar | listening | thinking | speaking
    foyd = Signal(str)        # foydalanuvchi aytgan matn
    javob = Signal(str)       # Onasi javobi / xabar
    daraja = Signal(float)    # mikrofon balandligi 0..1


def _rms(b: bytes) -> float:
    import numpy as np
    a = np.frombuffer(b, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def model_tayyor(s: Signallar) -> str:
    yol = os.path.join(MODEL_PAPKA, MODEL_NOMI)
    if os.path.isdir(yol):
        return yol
    os.makedirs(MODEL_PAPKA, exist_ok=True)
    zip_yol = os.path.join(MODEL_PAPKA, MODEL_NOMI + ".zip")
    s.holat.emit("xabar")
    s.javob.emit("Ovoz modeli yuklanmoqda (bir marta, ~50 MB)...")
    log("Model yuklanmoqda:", MODEL_URL)
    urllib.request.urlretrieve(MODEL_URL, zip_yol)
    with zipfile.ZipFile(zip_yol) as z:
        z.extractall(MODEL_PAPKA)
    os.remove(zip_yol)
    return yol


class Gapirgich:
    """Javobni jumlama-jumla ovozga aylantiradi. Birinchi jumla tayyor bo'lishi bilan
    gapira boshlaydi, keyingilarini esa shu vaqtda tayyorlab turadi (butun javobni kutmaydi).
    Gemini matni kelgan sari matn_qosh() ga beriladi, oxirida tugat() chaqiriladi."""

    def __init__(self, y):
        self.y = y
        self.buf = ""
        self.hammasi = ""
        self.t0 = time.time()
        self.birinchi_matn = False
        self.jq = queue.Queue()      # gapirilishi kerak jumlalar
        self.fq = queue.Queue()      # tayyor audio fayllar
        self.tts_t = threading.Thread(target=self._tts_sikl, daemon=True)
        self.pl_t = threading.Thread(target=self._pl_sikl, daemon=True)
        self.tts_t.start()
        self.pl_t.start()

    def matn_qosh(self, t: str):
        t = t.replace("*", "").replace("#", "")
        if not t:
            return
        if not self.birinchi_matn and t.strip():
            self.birinchi_matn = True
            log(f"Birinchi matn: {time.time() - self.t0:.1f}s")
        self.buf += t
        self.hammasi += t
        self.y.s.javob.emit(self.hammasi.strip())
        jumlalar, self.buf = jumlalarga_bol(self.buf)
        for j in jumlalar:
            self.jq.put(j)

    def tugat(self) -> str:
        if self.buf.strip():
            self.jq.put(self.buf.strip())
            self.buf = ""
        self.jq.put(None)
        self.tts_t.join()
        self.pl_t.join()
        return self.hammasi.strip()

    def _sintez(self, matn: str):
        """(fayl | None, keshdami)"""
        kesh = len(matn) <= 40
        if kesh:
            h = hashlib.md5(f"{OVOZ}|{matn}".encode("utf-8")).hexdigest()[:16]
            os.makedirs(KESH_PAPKA, exist_ok=True)
            yol = os.path.join(KESH_PAPKA, h + ".mp3")
            if os.path.exists(yol) and os.path.getsize(yol) > 0:
                return yol, True
        else:
            yol = os.path.join(tempfile.gettempdir(),
                               f"onasi_{int(time.time() * 1000)}_{threading.get_ident()}.mp3")
        try:
            asyncio.run(_tts(matn, yol))
            return yol, kesh
        except Exception as e:
            log("TTS xatosi:", e)
            try:
                os.remove(yol)
            except OSError:
                pass
            return None, False

    def _tts_sikl(self):
        while True:
            j = self.jq.get()
            if j is None or self.y.to_xta:
                self.fq.put(None)
                return
            yol, kesh = self._sintez(j)
            self.fq.put((yol, kesh, j))

    def _pl_sikl(self):
        boshladi = False
        try:
            import pygame
            while True:
                it = self.fq.get()
                if it is None:
                    break
                yol, kesh, matn = it
                if not boshladi:
                    boshladi = True
                    self.y.pauza = True
                    self.y.s.holat.emit("speaking")
                    log(f"Birinchi ovozgacha: {time.time() - self.t0:.1f}s")
                try:
                    if yol is None:
                        raise RuntimeError("audio yaratilmadi")
                    pygame.mixer.music.load(yol)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy() and not self.y.to_xta:
                        time.sleep(0.05)
                    pygame.mixer.music.unload()
                except Exception as e:
                    log("Ijro xatosi:", e)
                    time.sleep(min(6, 0.5 + len(matn) / 15))   # ovoz bo'lmasa ham matn o'qiladigan vaqt
                finally:
                    if yol and not kesh:
                        try:
                            os.remove(yol)
                        except OSError:
                            pass
        finally:
            if boshladi:
                self.y.pauza = False
                self.y.navbat_tozala()


class Yordamchi(threading.Thread):
    def __init__(self, s: Signallar):
        super().__init__(daemon=True)
        self.s = s
        self.q = queue.Queue(maxsize=400)
        self.pauza = False
        self.to_xta = False
        self.qol = threading.Event()
        self.tarix = []
        self.oxirgi = 0.0
        self.nf = 150.0          # fon shovqini darajasi

    # ---- audio ----
    def _cb(self, indata, frames, vaqt, status):
        if self.pauza:
            return
        try:
            self.q.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def blok(self, timeout=0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def navbat_tozala(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    @property
    def chegara(self) -> float:
        return max(MIN_CHEGARA, self.nf * 2.6)

    # ---- chiqish ovozi ----
    def gapir(self, matn: str):
        """Tayyor matnni gapiradi (qisqa jumlalar keshdan darrov o'qiladi)."""
        g = Gapirgich(self)
        g.matn_qosh(matn.strip())
        g.tugat()

    # ---- gapni yozib olish ----
    def yozib_ol(self, kutish: float, oldingi: bytes = None):
        if oldingi is None:
            self.navbat_tozala()
        bloklar, gapirdi, jim = [], False, 0
        if oldingi:
            bloklar.append(oldingi)
            gapirdi = True
        boshlandi = time.time()
        jim_blok = None
        while not self.to_xta and time.time() - boshlandi < MAX_SONIYA:
            data = self.blok(0.5)
            if data is None:
                if not gapirdi and time.time() - boshlandi > kutish:
                    return None
                continue
            rms = _rms(data)
            self.s.daraja.emit(min(1.0, rms / 5000.0))
            bloklar.append(data)
            if jim_blok is None:
                jim_blok = max(3, int(JIM_SONIYA / (len(data) / 2 / 16000.0)))
            if rms > self.chegara:
                gapirdi, jim = True, 0
            elif gapirdi:
                jim += 1
                if jim >= jim_blok:
                    break
            if not gapirdi and time.time() - boshlandi > kutish:
                return None
        return b"".join(bloklar) if gapirdi else None

    # ---- miya ----
    def javob_ol(self, matn: str, g: Gapirgich) -> str:
        """Javobni oqim bilan oladi va matn kelgan sari g (Gapirgich) orqali gapiradi."""
        if not gemini_bor():
            j = self.oddiy_javob(matn)
            g.matn_qosh(j)
            return j
        self.tarix.append({"role": "user", "parts": [{"text": matn}]})
        for _ in range(5):
            content = gemini_so_rov(self.tarix, on_text=g.matn_qosh)
            qismlar = content.get("parts", [])
            self.tarix.append({"role": "model", "parts": qismlar})
            chaqiruvlar = [p["functionCall"] for p in qismlar if "functionCall" in p]
            if not chaqiruvlar:
                self.tarix = tarixni_kes(self.tarix)
                if not g.hammasi.strip():
                    g.matn_qosh("Javob topa olmadim.")
                return g.hammasi.strip()
            if any("text" in p for p in qismlar):
                g.matn_qosh(" ")
            natijalar = []
            for c in chaqiruvlar:
                r = vosita_bajar(c["name"], c.get("args", {}) or {})
                natijalar.append({"functionResponse": {"name": c["name"],
                                                       "response": {"natija": r}}})
            self.tarix.append({"role": "user", "parts": natijalar})
        g.matn_qosh(" Kechirasiz, buni bajara olmadim.")
        return g.hammasi.strip()

    def oddiy_javob(self, matn: str) -> str:
        m = matn.lower()
        if any(s in m for s in ["reja", "vazifa", "nima qil", "ishlar"]):
            r = rejalar_ol(bugun_kaliti())
            return ("Bugungi ishlaringiz: " + "; ".join(r) + ".") if r else "Bugun uchun reja yo'q."
        if any(s in m for s in ["havo", "harorat", "yomg'ir", "yomgir"]):
            try:
                return ob_havo()
            except Exception:
                return "Ob-havoni olib bo'lmadi."
        return ("Bu savolga javob berish uchun bepul Gemini kaliti kerak. "
                "Hozircha rejalar va ob-havo haqida so'rashingiz mumkin.")

    # ---- suhbat ----
    def suhbat(self, oldingi: bytes = None):
        if time.time() - self.oxirgi > 600:
            self.tarix = []
        self.s.foyd.emit("")
        self.s.javob.emit("")
        birinchi, kutish = True, 6
        try:
            while not self.to_xta:
                self.s.holat.emit("listening")
                raw = self.yozib_ol(kutish, oldingi if birinchi else None)
                oldingi = None
                if raw is None:
                    break
                self.s.holat.emit("thinking")
                t_stt = time.time()
                matn = ovozni_matnga(raw)
                log(f"Ovozni matnga: {time.time() - t_stt:.1f}s")
                if matn is None:
                    self.gapir("Internet bilan muammo bor.")
                    break
                matn = ismni_olib_tashla(matn)
                if not matn:
                    if birinchi:
                        birinchi, kutish = False, 7
                        self.gapir("Eshitaman.")
                        continue
                    break
                birinchi, kutish = False, 7
                self.s.foyd.emit(matn)
                if qisqa_buyruq(matn, TUGATISH):
                    self.gapir("Marhamat!")
                    break
                g = Gapirgich(self)
                n0 = len(self.tarix)
                try:
                    self.javob_ol(matn, g)
                except Exception as e:
                    log("Javob xatosi:", e)
                    del self.tarix[n0:]          # bajarilmay qolgan almashinuvni o'chiramiz
                    if not g.hammasi.strip():
                        g.matn_qosh("Kechirasiz, hozir javob bera olmadim.")
                finally:
                    g.tugat()
                log(f"Javob tugadi: {time.time() - g.t0:.1f}s")
        finally:
            self.oxirgi = time.time()
            self.s.holat.emit("idle")

    # ---- asosiy sikl ----
    def run(self):
        try:
            self._run()
        except Exception as e:
            log("JIDDIY XATO:", repr(e))
            logging.exception("run")
            self.s.holat.emit("xabar")
            self.s.javob.emit(f"Xatolik: {e}")

    def _run(self):
        import pygame
        import sounddevice as sd
        import vosk
        import edge_tts  # noqa: F401  (oldindan yuklab qo'yamiz: birinchi javob tezroq bo'ladi)

        try:
            pygame.mixer.init()
        except Exception as e:
            log("pygame.mixer xatosi:", e)

        yol = model_tayyor(self.s)
        vosk.SetLogLevel(-1)
        rec = vosk.KaldiRecognizer(vosk.Model(yol), 16000)

        with sd.RawInputStream(samplerate=16000, blocksize=1280, dtype="int16",
                               channels=1, callback=self._cb):
            log("Onasi tayyor.")
            self.s.holat.emit("xabar")
            xabar = "Onasi tayyor. Meni «Onasi» deb chaqiring."
            if not gemini_bor():
                xabar += " (Gemini kaliti yo'q: faqat rejalar va ob-havo ishlaydi.)"
            self.s.javob.emit(xabar)

            while not self.to_xta:
                if self.qol.is_set():
                    self.qol.clear()
                    self.suhbat()
                    rec.Reset()
                    continue
                data = self.blok(0.5)
                if data is None:
                    continue
                rms = _rms(data)
                self.nf = 0.995 * self.nf + 0.005 * min(rms, self.nf * 1.6 + 50)
                if rec.AcceptWaveform(data):
                    matn = json.loads(rec.Result()).get("text", "")
                else:
                    matn = json.loads(rec.PartialResult()).get("partial", "")
                if matn and ism_bormi(matn):
                    log("Ism eshitildi:", matn)
                    rec.Reset()
                    self.suhbat(oldingi=data)
                    rec.Reset()


# ============================== OYNA (dizayn) ==============================
RANGLAR = {
    "idle": (120, 130, 160), "xabar": (120, 130, 160),
    "listening": (70, 200, 255), "thinking": (150, 110, 255), "speaking": (255, 110, 200),
}
YOZUVLAR = {"idle": "Onasi", "xabar": "Onasi", "listening": "Tinglayapman",
            "thinking": "O'ylayapman", "speaking": "Onasi"}


class Oyna(QWidget):
    KENGLIK = 480

    def __init__(self, s: Signallar):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.holat = "idle"
        self.faza = 0.0
        self.d = 0.0
        self.hedef_d = 0.0
        self.foyd = ""
        self.javob = ""
        self.maqsad = 0.0
        self.setWindowOpacity(0.0)
        self.resize(self.KENGLIK, 160)

        s.holat.connect(self.holat_o)
        s.foyd.connect(self.foyd_o)
        s.javob.connect(self.javob_o)
        s.daraja.connect(self.daraja_o)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tik)
        self.timer.start(16)

    # --- slotlar ---
    def holat_o(self, h: str):
        self.holat = h
        if h == "idle":
            QTimer.singleShot(3500, self.yashir)
        elif h == "xabar":
            self.korsat()
            QTimer.singleShot(5000, self.xabarni_yashir)
        else:
            self.korsat()

    def xabarni_yashir(self):
        if self.holat == "xabar":
            self.holat = "idle"
            self.maqsad = 0.0

    def yashir(self):
        if self.holat == "idle":
            self.maqsad = 0.0

    def korsat(self):
        self.maqsad = 1.0
        if not self.isVisible():
            self.show()
        self.moslash()

    def foyd_o(self, t: str):
        self.foyd = t
        self.javob = "" if t else self.javob
        self.moslash()

    def javob_o(self, t: str):
        self.javob = t
        self.moslash()

    def daraja_o(self, v: float):
        self.hedef_d = v

    # --- joylashuv ---
    def matn_(self) -> str:
        return self.javob or self.foyd

    def moslash(self):
        fm = QFontMetrics(QFont("Segoe UI", 11))
        matn_k = self.KENGLIK - 150 - 24
        br = fm.boundingRect(0, 0, matn_k, 4000, Qt.TextWordWrap, self.matn_() or " ")
        h = max(150, min(440, 16 + 30 + br.height() + 36))
        ekran = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(ekran.right() - self.KENGLIK - 24, ekran.bottom() - h - 24,
                         self.KENGLIK, int(h))

    # --- animatsiya ---
    def tik(self):
        self.faza += 0.016
        self.d += (self.hedef_d - self.d) * 0.3
        self.hedef_d *= 0.9
        o = self.windowOpacity()
        o += (self.maqsad - o) * 0.18
        self.setWindowOpacity(max(0.0, min(1.0, o)))
        if self.maqsad == 0.0 and o < 0.02 and self.isVisible():
            self.hide()
        if self.isVisible():
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(8, 8, self.width() - 16, self.height() - 16)
        yo = QPainterPath()
        yo.addRoundedRect(rect, 28, 28)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, QColor(18, 20, 32, 242))
        g.setColorAt(1, QColor(32, 24, 56, 242))
        p.fillPath(yo, g)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(yo)

        rang = QColor(*RANGLAR.get(self.holat, RANGLAR["idle"]))
        self.shar(p, rect.left() + 72, rect.center().y(), rang)

        matn_rect = QRectF(rect.left() + 142, rect.top() + 16, rect.width() - 142 - 20,
                           rect.height() - 28)
        p.setPen(rang.lighter(120))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(QRectF(matn_rect.left(), matn_rect.top(), matn_rect.width(), 18),
                   Qt.AlignLeft | Qt.AlignVCenter, YOZUVLAR.get(self.holat, "Onasi").upper())

        tana = QRectF(matn_rect.left(), matn_rect.top() + 24, matn_rect.width(),
                      matn_rect.height() - 24)
        if self.javob:
            p.setPen(QColor(240, 242, 250))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(tana, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self.javob)
        elif self.foyd:
            p.setPen(QColor(170, 176, 200))
            f = QFont("Segoe UI", 11)
            f.setItalic(True)
            p.setFont(f)
            p.drawText(tana, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self.foyd)
        p.end()

    def shar(self, p: QPainter, cx: float, cy: float, rang: QColor):
        t, h = self.faza, self.holat
        if h == "listening":
            r = 26 + self.d * 22 + math.sin(t * 3) * 1.5
        elif h == "thinking":
            r = 24 + math.sin(t * 4) * 2
        elif h == "speaking":
            r = 28 + math.sin(t * 7) * 4 + math.sin(t * 11.3) * 2.5
        else:
            r = 22 + math.sin(t * 1.5) * 1.2

        p.setPen(Qt.NoPen)
        glow = QRadialGradient(QPointF(cx, cy), r * 2.4)
        c1, c0 = QColor(rang), QColor(rang)
        c1.setAlpha(120)
        c0.setAlpha(0)
        glow.setColorAt(0, c1)
        glow.setColorAt(1, c0)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r * 2.4, r * 2.4)

        core = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.3), r * 1.4)
        core.setColorAt(0, QColor(255, 255, 255, 240))
        core.setColorAt(0.35, rang)
        core.setColorAt(1, QColor(rang).darker(230))
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), r, r)

        if h == "thinking":
            p.setBrush(QColor(210, 190, 255, 225))
            for i in range(3):
                a = t * 3 + i * 2.094
                p.drawEllipse(QPointF(cx + math.cos(a) * (r + 14), cy + math.sin(a) * (r + 14)),
                              3.5, 3.5)
        if h == "speaking":
            p.setBrush(Qt.NoBrush)
            for i in range(2):
                k = (t * 0.9 + i * 0.5) % 1.0
                c = QColor(rang)
                c.setAlpha(int(130 * (1 - k)))
                p.setPen(QPen(c, 2))
                p.drawEllipse(QPointF(cx, cy), r + k * 24, r + k * 24)


# ============================== ASOSIY ==============================
def tray_ikonka() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(QPointF(24, 24), 40)
    g.setColorAt(0, QColor(255, 255, 255))
    g.setColorAt(0.4, QColor(150, 110, 255))
    g.setColorAt(1, QColor(70, 40, 160))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(32, 32), 28, 28)
    p.end()
    return QIcon(pm)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    lock = QLockFile(os.path.join(tempfile.gettempdir(), "onasi.lock"))
    if not lock.tryLock(100):
        return                     # Onasi allaqachon ishlayapti

    s = Signallar()
    oyna = Oyna(s)
    yord = Yordamchi(s)

    tray = QSystemTrayIcon(tray_ikonka(), app)
    tray.setToolTip("Onasi: «Onasi» deb chaqiring")
    menyu = QMenu()
    a1 = QAction("Onasini chaqirish")
    a1.triggered.connect(yord.qol.set)
    a2 = QAction("Chiqish")
    a2.triggered.connect(app.quit)
    menyu.addAction(a1)
    menyu.addSeparator()
    menyu.addAction(a2)
    tray.setContextMenu(menyu)
    tray.activated.connect(lambda r: yord.qol.set() if r == QSystemTrayIcon.Trigger else None)
    tray.show()

    def toxtat():
        yord.to_xta = True
    app.aboutToQuit.connect(toxtat)

    yord.start()
    code = app.exec()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
