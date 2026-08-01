import os
import asyncio
import logging
import uuid
import subprocess
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, FSInputFile, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from aiogram.enums import ParseMode
import yt_dlp
import requests
from shazamio import Shazam

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AUDD_API_TOKEN = os.getenv("AUDD_API_TOKEN")  # ixtiyoriy

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_links = {}
QUALITIES = [360, 480, 720, 1080]


def get_base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "ios", "mweb"],
            },
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
        "format_sort": ["asr", "abr", "size"],
    }
    if os.path.exists("cookies.txt"):
        print("✅ cookies.txt topildi")
        opts["cookiefile"] = "cookies.txt"
    else:
        print("❌ cookies.txt topilmadi")
    return opts


def estimate_size(duration: int, height: int) -> int:
    if not duration or duration <= 0:
        return 0
    bitrate = {360: 1.2, 480: 2.8, 720: 5.5, 1080: 9.0}.get(height, 3.5)
    return int(bitrate * 1_000_000 / 8 * duration)


def get_video_info(url: str) -> dict:
    opts = get_base_opts()
    opts["ignore_no_formats_error"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise Exception(f"Video ma'lumotini olishda xato: {str(e)[:180]}")

    if not info:
        raise Exception("Video ma'lumoti topilmadi")

    title = (info.get("title") or "Video")[:80]

    # ========== DURATION ni maksimal darajada olish ==========
    duration = 0

    # 1. Asosiy maydon
    if info.get("duration") and info["duration"] > 0:
        duration = int(info["duration"])

    # 2. Formatlardan eng katta duration ni olish
    if duration <= 0:
        max_dur = 0
        for f in info.get("formats") or []:
            d = f.get("duration")
            if d and isinstance(d, (int, float)) and d > max_dur:
                max_dur = d
        if max_dur > 0:
            duration = int(max_dur)

    # 3. duration_string ("1:23", "0:45" va h.k.)
    if duration <= 0:
        dur_str = info.get("duration_string") or info.get("approx_duration")
        if isinstance(dur_str, str):
            dur_str = dur_str.strip()
            try:
                if ":" in dur_str:
                    parts = [int(p) for p in dur_str.split(":")]
                    if len(parts) == 2:
                        duration = parts[0] * 60 + parts[1]
                    elif len(parts) == 3:
                        duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
                else:
                    duration = int(float(dur_str))
            except (ValueError, TypeError):
                pass

    # 4. Instagram maxsus — ba'zi hollarda "entries" ichida bo'ladi
    if duration <= 0 and info.get("entries"):
        for entry in info["entries"]:
            if entry and entry.get("duration") and entry["duration"] > 0:
                duration = int(entry["duration"])
                break

    if duration <= 0:
        # ba'zi Instagram videolarida "video_duration" yoki "length" bo'ladi
        for key in ("video_duration", "length", "approx_duration_ms"):
            val = info.get(key)
            if val:
                try:
                    if key.endswith("_ms"):
                        duration = int(float(val) / 1000)
                    else:
                        duration = int(float(val))
                    if duration > 0:
                        break
                except (ValueError, TypeError):
                    pass

    duration = int(duration) if duration else 0

    # Sifatlar va hajmlar
    size_map = {}
    for f in info.get("formats") or []:
        h = f.get("height")
        if not h or f.get("vcodec") == "none":
            continue
        sz = f.get("filesize") or f.get("filesize_approx") or 0
        if h not in size_map or (sz and sz > size_map.get(h, 0)):
            size_map[h] = sz

    for q in QUALITIES:
        if q not in size_map or not size_map[q]:
            size_map[q] = estimate_size(duration, q)

    # Audio hajmi
    audio_size = 0
    for f in info.get("formats") or []:
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            sz = f.get("filesize") or f.get("filesize_approx") or 0
            if sz > audio_size:
                audio_size = sz
    if not audio_size and duration:
        audio_size = int(duration * 192 * 1000 / 8)

    # Thumbnail
    thumb = info.get("thumbnail")
    if not thumb and info.get("thumbnails"):
        thumb = info["thumbnails"][-1].get("url")

    # Instagram metadata dan musiqa
    music_info = None
    track = info.get("track") or info.get("alt_title")
    artist = info.get("artist") or info.get("creator") or info.get("uploader")
    if track:
        music_info = {
            "title": track,
            "artist": artist or "Noma'lum",
            "source": "instagram"
        }

    return {
        "title": title,
        "duration": duration,
        "size_map": size_map,
        "audio_size": audio_size,
        "thumbnail": thumb,
        "music_info": music_info,
    }


def download_media(url: str, height: int = None, is_audio: bool = False) -> dict:
    opts = get_base_opts()

    if is_audio:
        format_str = "bestaudio/best"
    elif height is None:
        # Eng yaxshi sifat (Instagram uchun)
        format_str = (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4]/"
            "best"
        )
    else:
        format_str = (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"bestvideo+bestaudio/best"
        )

    opts["outtmpl"] = f"{DOWNLOAD_DIR}/%(id)s_{height or 'best'}.%(ext)s"
    opts["format"] = format_str
    opts["merge_output_format"] = "mp4"
    opts["concurrent_fragment_downloads"] = 8
    opts["retries"] = 5

    if is_audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["postprocessors"] = [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }]
        opts["postprocessor_args"] = {
            "ffmpeg": ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
        }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if is_audio:
            base = os.path.splitext(filename)[0]
            mp3_path = base + ".mp3"
            if os.path.exists(mp3_path):
                filename = mp3_path
            else:
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith(".mp3") and info.get("id", "") in f:
                        filename = os.path.join(DOWNLOAD_DIR, f)
                        break
        else:
            if not filename.endswith(".mp4"):
                base = os.path.splitext(filename)[0]
                if os.path.exists(base + ".mp4"):
                    filename = base + ".mp4"
            if not os.path.exists(filename):
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith((".mp4", ".webm", ".mkv")) and info.get("id", "") in f:
                        filename = os.path.join(DOWNLOAD_DIR, f)
                        break

        if not os.path.exists(filename):
            raise Exception("Fayl yuklab olinmadi")

        size = os.path.getsize(filename)
        return {
            "title": (info.get("title") or "File")[:80],
            "filename": filename,
            "size_mb": round(size / (1024 * 1024), 1),
            "height": info.get("height") or height,
            "duration": info.get("duration") or 0,
            "id": info.get("id"),
        }

async def recognize_with_shazam(filepath: str) -> dict | None:
    """Videoning turli qismlaridan Shazam orqali musiqa aniqlash"""

    if not os.path.exists(filepath):
        logging.warning(f"[SHAZAM] Fayl topilmadi: {filepath}")
        return None

    # ---------------------------------------------------------
    # VIDEO DAVOMIYLIGINI ANIQLASH
    # ---------------------------------------------------------

    duration = 0

    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filepath
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

        if probe.returncode == 0 and probe.stdout.strip():
            duration = float(probe.stdout.strip())

    except Exception as e:
        logging.warning(
            f"[SHAZAM] Video davomiyligini aniqlab bo'lmadi: {e}"
        )

    print(f"[SHAZAM] Video davomiyligi: {duration:.1f} sekund")

    # ---------------------------------------------------------
    # TEKSHIRILADIGAN SEGMENTLAR
    # ---------------------------------------------------------

    segments = []

    if duration <= 0:
        segments = [0]

    elif duration <= 20:
        segments = [0]

    else:
        # Video boshidan
        segments.append(0)

        # 5-soniyadan
        if duration > 25:
            segments.append(5)

        # 15-soniyadan
        if duration > 35:
            segments.append(15)

        # Video o'rtasi
        middle = max(0, int(duration / 2) - 10)
        segments.append(middle)

        # Video oxiridan 20 sekund
        end_start = max(0, int(duration) - 20)
        segments.append(end_start)

    # Bir xil vaqtlarni olib tashlaymiz
    segments = sorted(set(segments))

    print(
        f"[SHAZAM] Tekshiriladigan segmentlar: {segments}"
    )

    # ---------------------------------------------------------
    # SHAZAM
    # ---------------------------------------------------------

    shazam = Shazam()

    for index, start_time in enumerate(segments, start=1):

        audio_path = os.path.join(
            DOWNLOAD_DIR,
            f"shazam_{uuid.uuid4().hex[:8]}.mp3"
        )

        try:

            print(
                f"[SHAZAM] {index}/{len(segments)} "
                f"→ {start_time}s dan tekshirilmoqda..."
            )

            # -------------------------------------------------
            # AUDIO AJRATISH
            # -------------------------------------------------

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",

                    "-ss", str(start_time),
                    "-i", filepath,

                    "-vn",

                    # Shazam uchun yaxshi format
                    "-ac", "2",
                    "-ar", "44100",

                    "-acodec", "libmp3lame",
                    "-b:a", "192k",

                    # 20 sekundlik parcha
                    "-t", "20",

                    audio_path
                ],
                check=True,
                capture_output=True,
                timeout=60
            )

            # Fayl yaratilganini tekshirish
            if not os.path.exists(audio_path):
                print(
                    "[SHAZAM] Audio fayl yaratilmagan."
                )
                continue

            # Juda kichik fayl bo'lsa o'tkazib yuboramiz
            if os.path.getsize(audio_path) < 5000:
                print(
                    "[SHAZAM] Audio fayl juda kichik."
                )
                continue

            # -------------------------------------------------
            # SHAZAMGA YUBORISH
            # -------------------------------------------------

            result = await shazam.recognize(audio_path)

            print(
                f"[SHAZAM] Natija: {result}"
            )

            if not result:
                print(
                    "[SHAZAM] Bu segmentda musiqa topilmadi."
                )
                continue

            if "track" not in result:
                print(
                    "[SHAZAM] Track topilmadi."
                )
                continue

            track = result["track"]

            title = (
                track.get("title") or ""
            ).strip()

            artist = (
                track.get("subtitle") or ""
            ).strip()

            if not title:
                continue

            if not artist:
                artist = "Noma'lum"

            print(
                f"[SHAZAM] ✅ MUSIQA TOPILDI:"
                f" {artist} - {title}"
            )

            return {
                "title": title,
                "artist": artist,
                "url": track.get("url", ""),
                "image": (
                    track.get("images", {}).get("coverarthq")
                    or track.get("images", {}).get("coverart")
                    or ""
                ),
                "source": "shazam"
            }

        except Exception as e:

            logging.warning(
                f"[SHAZAM] {start_time}s segment xatosi: {e}"
            )

            # Bitta segment xato bersa,
            # keyingisini tekshirishda davom etamiz.
            continue

        finally:

            # Vaqtinchalik MP3 ni o'chirish
            if os.path.exists(audio_path):

                try:
                    os.remove(audio_path)

                except Exception as e:
                    logging.warning(
                        f"[SHAZAM] Vaqtinchalik fayl "
                        f"o'chirilmadi: {e}"
                    )

    # ---------------------------------------------------------
    # HECH QAYSI SEGMENTDA TOPILMADI
    # ---------------------------------------------------------

    print(
        "[SHAZAM] ❌ Hech qaysi segmentdan "
        "musiqa aniqlanmadi."
    )

    return None


def recognize_with_audd(filepath: str) -> dict | None:
    """AudD orqali musiqani aniqlash — Shazam uchun zaxira."""

    if not AUDD_API_TOKEN:
        print("[AUDD] ❌ AUDD_API_TOKEN mavjud emas!")
        return None

    if not os.path.exists(filepath):
        print(f"[AUDD] ❌ Fayl topilmadi: {filepath}")
        return None

    audio_path = os.path.join(
        DOWNLOAD_DIR,
        f"audd_{uuid.uuid4().hex[:8]}.mp3"
    )

    try:

        print("[AUDD] 🎵 Audio tayyorlanmoqda...")

        # -----------------------------------------------------
        # Videodan sifatli audio ajratamiz
        # -----------------------------------------------------

        subprocess.run(
            [
                "ffmpeg",
                "-y",

                "-i", filepath,

                "-vn",

                "-ac", "2",
                "-ar", "44100",

                "-acodec", "libmp3lame",
                "-b:a", "192k",

                # AudD uchun 20 sekund yetarli
                "-t", "20",

                audio_path
            ],
            check=True,
            capture_output=True,
            timeout=60
        )

        if not os.path.exists(audio_path):
            print("[AUDD] ❌ Audio fayl yaratilmadi.")
            return None

        size = os.path.getsize(audio_path)

        print(
            f"[AUDD] Audio tayyor: "
            f"{round(size / 1024, 1)} KB"
        )

        if size < 5000:
            print("[AUDD] ❌ Audio fayl juda kichik.")
            return None

        # -----------------------------------------------------
        # AudD API
        # -----------------------------------------------------

        print("[AUDD] 🔍 AudD orqali musiqa qidirilmoqda...")

        with open(audio_path, "rb") as f:

            resp = requests.post(
                "https://api.audd.io/",
                data={
                    "api_token": AUDD_API_TOKEN,
                    "return": "apple_music,spotify"
                },
                files={
                    "file": (
                        "audio.mp3",
                        f,
                        "audio/mpeg"
                    )
                },
                timeout=60
            )

        print(
            f"[AUDD] HTTP status: {resp.status_code}"
        )

        # -----------------------------------------------------
        # JSON javob
        # -----------------------------------------------------

        try:
            data = resp.json()
        except Exception:

            print(
                "[AUDD] ❌ API JSON qaytarmadi:"
            )

            print(
                resp.text[:1000]
            )

            return None

        print(
            f"[AUDD] API javobi: {data}"
        )

        # -----------------------------------------------------
        # SUCCESS
        # -----------------------------------------------------

        if (
            data.get("status") == "success"
            and data.get("result")
        ):

            result = data["result"]

            title = (
                result.get("title")
                or ""
            ).strip()

            artist = (
                result.get("artist")
                or ""
            ).strip()

            if not title:
                print(
                    "[AUDD] ❌ Natija bor, "
                    "lekin title yo'q."
                )
                return None

            if not artist:
                artist = "Noma'lum"

            print(
                f"[AUDD] ✅ MUSIQA TOPILDI: "
                f"{artist} - {title}"
            )

            return {
                "title": title,
                "artist": artist,
                "source": "audd",

                # Qo'shimcha ma'lumotlar
                "url": result.get("song_link", ""),
                "image": "",
            }

        # -----------------------------------------------------
        # MUSIQA TOPILMADI
        # -----------------------------------------------------

        if data.get("status") == "success":

            print(
                "[AUDD] ❌ AudD ham musiqani aniqlay olmadi."
            )

        else:

            print(
                "[AUDD] ❌ AudD API xatosi:"
            )

            print(
                data.get("error")
                or data
            )

        return None

    except requests.exceptions.Timeout:

        print(
            "[AUDD] ❌ API timeout."
        )

        return None

    except requests.exceptions.RequestException as e:

        print(
            f"[AUDD] ❌ Internet/API xatosi: {e}"
        )

        return None

    except Exception as e:

        logging.exception(
            f"[AUDD] Kutilmagan xato: {e}"
        )

        return None

    finally:

        if os.path.exists(audio_path):

            try:
                os.remove(audio_path)

            except Exception:
                pass


async def recognize_music(filepath: str) -> dict | None:
    """
    Musiqani aniqlash:
    1. Shazam
    2. Shazam topmasa AudD
    """

    print("=" * 60)
    print("[MUSIC] 🎵 Musiqani aniqlash boshlandi")

    # ---------------------------------------------------------
    # 1. SHAZAM
    # ---------------------------------------------------------

    print("[MUSIC] 1️⃣ Shazam tekshirilmoqda...")

    try:

        result = await recognize_with_shazam(filepath)

        if result:

            print(
                f"[MUSIC] ✅ Shazam topdi: "
                f"{result.get('artist')} - "
                f"{result.get('title')}"
            )

            return result

        print(
            "[MUSIC] ❌ Shazam topmadi."
        )

    except Exception as e:

        logging.exception(
            f"[MUSIC] Shazam xatosi: {e}"
        )

    # ---------------------------------------------------------
    # 2. AUDD
    # ---------------------------------------------------------

    print(
        "[MUSIC] 2️⃣ Shazam topmadi → AudD ishga tushmoqda..."
    )

    try:

        result = await asyncio.to_thread(
            recognize_with_audd,
            filepath
        )

        if result:

            print(
                f"[MUSIC] ✅ AudD topdi: "
                f"{result.get('artist')} - "
                f"{result.get('title')}"
            )

            return result

        print(
            "[MUSIC] ❌ AudD ham topmadi."
        )

    except Exception as e:

        logging.exception(
            f"[MUSIC] AudD xatosi: {e}"
        )

    # ---------------------------------------------------------
    # HECH QAYSI SERVIS TOPMADI
    # ---------------------------------------------------------

    print(
        "[MUSIC] ❌ Shazam ham, AudD ham "
        "musiqani aniqlay olmadi."
    )

    print("=" * 60)

    return None



def upload_large_file(filepath: str) -> str:
    filename = os.path.basename(filepath)
    try:
        r = requests.get("https://api.gofile.io/servers", timeout=15)
        servers = r.json()["data"]["servers"]
        for server_info in servers[:3]:
            server = server_info["name"]
            try:
                with open(filepath, "rb") as f:
                    resp = requests.post(
                        f"https://{server}.gofile.io/contents/uploadfile",
                        files={"file": (filename, f)},
                        timeout=600
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok":
                        return data["data"]["downloadPage"]
            except Exception:
                continue
    except Exception as e:
        logging.warning(f"GoFile xatosi: {e}")

    raise Exception("Faylni serverga yuklab bo‘lmadi")


def human_size(num: int) -> str:
    if not num or num <= 0:
        return "~?"
    mb = num / (1024 * 1024)
    if mb < 1:
        return f"{max(1, int(num / 1024))}KB"
    return f"{round(mb)}MB"


def build_keyboard(link_id: str, size_map: dict, audio_size: int, duration: int = 0) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for q in QUALITIES:
        sz = size_map.get(q, 0)
        if not sz:
            for h, s in size_map.items():
                if abs(h - q) <= 50:
                    sz = s
                    break
        btn = InlineKeyboardButton(
            text=f"🎬 {q}p • {human_size(sz)}",
            callback_data=f"dl:{link_id}:{q}"
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text=f"🎵 MP3 • {human_size(audio_size)}",
            callback_data=f"dl:{link_id}:mp3"
        )
    ])

    # ===== YANGI: Shorts (1:10 dan kam) uchun to‘liq musiqa tugmasi =====
    if 0 < duration < 70:          # 1:10 = 70 sekund
        buttons.append([
            InlineKeyboardButton(
                text="🎧 To‘liq musiqani topish",
                callback_data=f"findfull:{link_id}"
            )
        ])
    # ===================================================================

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_instagram_keyboard(link_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📥 Videoni Yuklab olish",
                callback_data=f"ig_video:{link_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎵 Audioni Yuklab olish",
                callback_data=f"ig_audio:{link_id}"
            )
        ]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "✨ <b>Video & Audio Downloader</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎬 YouTube va Instagram videolarini\n"
        "yuqori sifatda yuklab oling!\n\n"
        "📌 <b>Qanday ishlaydi?</b>\n"
        "1️⃣ Video linkini yuboring\n"
        "2️⃣ Kerakli sifatni tanlang\n"
        "3️⃣ Tayyor faylni oling\n\n"
        "💎 <b>Imkoniyatlar:</b>\n"
        "• 360p / 480p / 720p / 1080p\n"
        "• MP3 audio format\n"
        "• Video ichidagi musiqani topish (Shazam)\n"
        "• Tez va sifatli yuklash\n\n"
        "🔗 <i>Linkni yuboring va boshlang...</i>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(F.text)
async def on_url(message: Message):
    url = message.text.strip()
    is_instagram = any(x in url for x in ("instagram.com", "instagr.am"))
    is_youtube = any(x in url for x in ("youtube.com", "youtu.be"))

    if not (is_instagram or is_youtube):
        await message.answer("Mani sinamoqchimisan 😁 faqat YouTube bilan Instagramdan yubor !.")
        return

    status = await message.answer("🔍 Tayyor bo'lmoqda...")
    print(f"[INFO] Link qabul qilindi: {url}")

    try:
        info = await asyncio.to_thread(get_video_info, url)
        link_id = uuid.uuid4().hex[:8]
        user_links[link_id] = {
            "url": url,
            "thumbnail": info.get("thumbnail"),
            "title": info["title"],
            "music_info": info.get("music_info"),
            "is_instagram": is_instagram,
        }

        duration = info["duration"] or 0
        mins = duration // 60
        secs = duration % 60

        text = (
            f"<b>{info['title']}</b>\n"
            f"⏱ Davomiyligi: <b>{mins}:{secs:02d}</b>\n"
        )

        if info.get("music_info"):
            m = info["music_info"]
            text += f"\n🎵 <b>{m['artist']} — {m['title']}</b>"

        # ===== INSTAGRAM uchun maxsus =====
        if is_instagram:
            text += "\n\nQuyidagilardan birini tanlang:"
            kb = build_instagram_keyboard(link_id)

            thumb = info.get("thumbnail")
            if thumb:
                try:
                    await status.delete()
                    await message.answer_photo(
                        photo=thumb,
                        caption=text,
                        reply_markup=kb,
                        parse_mode=ParseMode.HTML
                    )
                    print(f"[INFO] Instagram thumbnail yuborildi: {thumb[:80]}...")
                    return
                except Exception as e:
                    print(f"[WARN] Thumbnail yuborib bo‘lmadi: {e}")
                    # thumbnail ishlamasa oddiy text bilan davom etamiz

            await status.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            return

        # ===== YouTube uchun eski sifat tanlash =====
        kb = build_keyboard(link_id, info["size_map"], info["audio_size"], duration)
        text += "\nSifatni tanlang:"
        await status.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.exception(e)
        print(f"[ERROR] get_video_info: {e}")
        await status.edit_text(f"❌ Xatolik:\n<code>{e}</code>", parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("ig_video:"))
async def on_ig_video(callback: CallbackQuery):
    await callback.answer()
    link_id = callback.data.split(":")[1]
    url_data = user_links.get(link_id)

    if not url_data:
        await callback.message.answer("❌ Link eskirgan. Qaytadan yuboring.")
        return

    url = url_data["url"]
    status = await callback.message.answer("⏳ Video yuklab olinmoqda...")
    print(f"[INFO] Instagram VIDEO yuklanmoqda: {url}")

    try:
        result = await asyncio.to_thread(download_media, url, height=None, is_audio=False)
        filepath = result["filename"]
        size_mb = result["size_mb"]
        print(f"[INFO] Video yuklandi: {filepath} ({size_mb} MB)")

        # ========== Haqiqiy duration ni ffprobe bilan olish ==========
        real_duration = result.get("duration") or 0
        if real_duration <= 0 and os.path.exists(filepath):
            try:
                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        filepath
                    ],
                    capture_output=True, text=True, timeout=15
                )
                if probe.returncode == 0 and probe.stdout.strip():
                    real_duration = int(float(probe.stdout.strip()))
                    print(f"[INFO] ffprobe duration: {real_duration}s")
            except Exception as e:
                print(f"[WARN] ffprobe ishlamadi: {e}")

        mins = real_duration // 60
        secs = real_duration % 60
        duration_text = f"{mins}:{secs:02d}"
        # ============================================================

        # Katta fayl bo'lsa GoFile ga yuklash
        if size_mb > 49:
            await status.edit_text("📤 Fayl katta, serverga yuklanmoqda...")
            try:
                link = await asyncio.to_thread(upload_large_file, filepath)
                caption = (
                    f"🎬 <b>{result['title']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱ {duration_text}  •  📦 {size_mb} MB\n"
                    f"✨ Premium yuklandi"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Videoni yuklab olish", url=link)]
                ])
                await callback.message.answer(caption, reply_markup=kb, parse_mode=ParseMode.HTML)
                await status.delete()
                print("[SUCCESS] Katta video GoFile orqali yuborildi")
            except Exception as e:
                logging.exception(e)
                print(f"[ERROR] GoFile yuklash: {e}")
                await status.edit_text(f"❌ Fayl juda katta ({size_mb} MB). Keyinroq urinib ko‘ring.")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Oddiy hajmdagi video
        file = FSInputFile(filepath)
        caption = (
            f"🎬 <b>{result['title']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ {duration_text}  •  📦 {size_mb} MB\n"
            f"✨ Premium yuklandi"
        )

        await callback.message.answer_video(
            video=file,
            duration=real_duration if real_duration > 0 else None,
            caption=caption,
            parse_mode=ParseMode.HTML,
            supports_streaming=True
        )
        print("[SUCCESS] Video Telegramga yuborildi")

        if os.path.exists(filepath):
            os.remove(filepath)
        await status.delete()

    except Exception as e:
        logging.exception(e)
        print(f"[ERROR] ig_video: {e}")
        err_text = str(e).replace("<", "&lt;").replace(">", "&gt;")
        try:
            await status.edit_text(f"❌ Xatolik:\n<code>{err_text}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            await status.edit_text(f"❌ Xatolik: {err_text}")

@dp.callback_query(F.data.startswith("ig_audio:"))
async def on_ig_audio(callback: CallbackQuery):
    await callback.answer()

    link_id = callback.data.split(":", 1)[1]
    url_data = user_links.get(link_id)

    if not url_data:
        await callback.message.answer(
            "❌ Bu link eskirgan.\n\n"
            "Iltimos, Instagram linkini qaytadan yuboring."
        )
        return

    url = url_data["url"]

    status = await callback.message.answer(
        "⏳ <b>Instagram videosi yuklanmoqda...</b>",
        parse_mode=ParseMode.HTML
    )

    filepath = None
    filename = None

    try:
        print("=" * 60)
        print(f"[INFO] Instagram AUDIO boshlandi")
        print(f"[INFO] URL: {url}")

        # =========================================================
        # 1. INSTAGRAM VIDEOSINI YUKLAB OLISH
        # =========================================================

        result = await asyncio.to_thread(
            download_media,
            url,
            height=None,
            is_audio=False
        )

        filepath = result["filename"]

        print(f"[INFO] Video yuklandi: {filepath}")

        if not os.path.exists(filepath):
            raise Exception("Instagram videosi yuklab olinmadi.")

        # =========================================================
        # 2. SHAZAM / AUDD ORQALI MUSIQANI ANIQLASH
        # =========================================================

        await status.edit_text(
            "🎵 <b>Videodagi musiqa aniqlanmoqda...</b>",
            parse_mode=ParseMode.HTML
        )

        music = await recognize_music(filepath)

        print(f"[INFO] Shazam/AudD natijasi: {music}")

        # Agar Shazam topmasa, Instagram metadata
        if not music:
            music = url_data.get("music_info")
            print(f"[INFO] Instagram metadata natijasi: {music}")

        # =========================================================
        # 3. MUSIQA TOPILMAGAN BO'LSA
        # =========================================================

        if not music or not music.get("title"):
            await status.edit_text(
                "❌ <b>Musiqani aniqlab bo'lmadi.</b>\n\n"
                "Videoda musiqa juda qisqa yoki Shazam uni taniy olmadi.",
                parse_mode=ParseMode.HTML
            )

            if filepath and os.path.exists(filepath):
                os.remove(filepath)

            return

        artist = (music.get("artist") or "").strip()
        title = (music.get("title") or "").strip()

        # Artist + title
        if artist and artist.lower() not in ["unknown", "noma'lum"]:
            query = f"{artist} - {title}"
        else:
            query = title

        print(f"[INFO] Aniqlangan musiqa: {query}")

        # =========================================================
        # 4. QUERY NI TOZALASH
        # =========================================================

        import re

        clean_query = query

        # HTML / maxsus belgilarni olib tashlash
        clean_query = re.sub(r"<[^>]+>", "", clean_query)

        # Keraksiz belgilarni bo'sh joyga almashtirish
        clean_query = re.sub(
            r"[\"'`|\\/\[\]\{\}\(\)<>#@*_+=~^]",
            " ",
            clean_query
        )

        # Emoji va boshqa g'alati belgilarni saqlamaslik
        clean_query = re.sub(
            r"[^\w\s\-.,!?&]",
            " ",
            clean_query,
            flags=re.UNICODE
        )

        # Ortiqcha bo'shliqlar
        clean_query = re.sub(r"\s+", " ", clean_query).strip()

        # Juda uzun query
        if len(clean_query) > 100:
            clean_query = clean_query[:100].strip()

        if not clean_query:
            raise Exception("Musiqa nomi bo'sh chiqdi.")

        print(f"[INFO] Tozalangan YouTube query: {clean_query}")

        await status.edit_text(
            f"🔍 <b>{clean_query}</b>\n\n"
            "YouTube'dan to'liq qo'shiq qidirilmoqda...",
            parse_mode=ParseMode.HTML
        )

        # =========================================================
        # 5. UNIQUE FOLDER / FILE NOMI
        # =========================================================

        unique_id = uuid.uuid4().hex[:12]

        output_template = os.path.join(
            DOWNLOAD_DIR,
            f"{unique_id}_song.%(ext)s"
        )

        # =========================================================
        # 6. YOUTUBE OPTIONS
        # =========================================================

        opts = get_base_opts()

        opts.update({
            "format": (
                "bestaudio[ext=m4a]/"
                "bestaudio[ext=webm]/"
                "bestaudio[ext=opus]/"
                "bestaudio/best"
            ),

            "outtmpl": output_template,

            "default_search": "ytsearch1",

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "retries": 5,

            "fragment_retries": 5,

            "continuedl": True,

            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        })

        # =========================================================
        # 7. YOUTUBE DAN TO'LIQ QO'SHIQNI QIDIRISH
        # =========================================================

        print(
            f"[INFO] YouTube qidiruv: "
            f"ytsearch1:{clean_query}"
        )

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:

                search_result = ydl.extract_info(
                    f"ytsearch1:{clean_query}",
                    download=False
                )

                if not search_result:
                    raise Exception(
                        "YouTube'dan hech qanday natija topilmadi."
                    )

                entries = search_result.get("entries")

                if not entries:
                    raise Exception(
                        f"YouTube'da «{clean_query}» topilmadi."
                    )

                video_info = entries[0]

                if not video_info:
                    raise Exception(
                        "YouTube qidiruv natijasi bo'sh."
                    )

                print(
                    f"[INFO] YouTube topildi: "
                    f"{video_info.get('title')}"
                )

                # Endi aynan topilgan videoni yuklaymiz
                info = ydl.extract_info(
                    video_info["webpage_url"],
                    download=True
                )

        except Exception as e:

            err_msg = str(e)

            print(
                f"[ERROR] YouTube yuklash xatosi: "
                f"{err_msg}"
            )

            # YouTube bot tekshiruvi
            if (
                "Sign in to confirm" in err_msg
                or "confirm you're not a bot" in err_msg
                or "not a bot" in err_msg
            ):

                await status.edit_text(
                    "❌ <b>YouTube botni aniqladi.</b>\n\n"
                    "cookies.txt faylingizni yangilash kerak.",
                    parse_mode=ParseMode.HTML
                )

            elif "Requested format is not available" in err_msg:

                await status.edit_text(
                    "❌ <b>Audio formati mavjud emas.</b>\n\n"
                    "Boshqa YouTube natijasi bilan urinib ko'rish kerak.",
                    parse_mode=ParseMode.HTML
                )

            else:

                safe_error = (
                    err_msg
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )

                await status.edit_text(
                    f"❌ <b>Qo'shiqni yuklashda xato:</b>\n\n"
                    f"<code>{safe_error[:800]}</code>",
                    parse_mode=ParseMode.HTML
                )

            if filepath and os.path.exists(filepath):
                os.remove(filepath)

            return

        # =========================================================
        # 8. MP3 FAYLNI FAQAT SHU UNIQUE ID ORQALI TOPAMIZ
        # =========================================================

        expected_mp3 = os.path.join(
            DOWNLOAD_DIR,
            f"{unique_id}_song.mp3"
        )

        # FFmpeg tugashini kutish
        for _ in range(30):

            if os.path.exists(expected_mp3):
                break

            await asyncio.sleep(0.5)

        if os.path.exists(expected_mp3):

            filename = expected_mp3

        else:

            # Agar nom boshqacha chiqsa, faqat unique_id bo'yicha
            # qidiramiz. Eski MP3 fayllarni olmaymiz.

            candidates = []

            for f in os.listdir(DOWNLOAD_DIR):

                if (
                    unique_id in f
                    and f.lower().endswith(".mp3")
                ):
                    candidates.append(
                        os.path.join(DOWNLOAD_DIR, f)
                    )

            if candidates:

                filename = max(
                    candidates,
                    key=os.path.getmtime
                )

        # =========================================================
        # 9. FAYL TOPILGANINI TEKSHIRISH
        # =========================================================

        if not filename or not os.path.exists(filename):

            print(
                "[ERROR] MP3 topilmadi."
            )

            print(
                "[DEBUG] downloads papkasidagi fayllar:"
            )

            for f in os.listdir(DOWNLOAD_DIR):
                print("   ", f)

            await status.edit_text(
                "❌ <b>Qo'shiq yuklandi, lekin MP3 fayl "
                "topilmadi.</b>\n\n"
                "FFmpeg ishlashida muammo bo'lishi mumkin.",
                parse_mode=ParseMode.HTML
            )

            if filepath and os.path.exists(filepath):
                os.remove(filepath)

            return

        # =========================================================
        # 10. FILE HAJMI
        # =========================================================

        file_size = os.path.getsize(filename)

        if file_size <= 0:
            raise Exception(
                "MP3 fayl bo'sh."
            )

        size_mb = round(
            file_size / (1024 * 1024),
            1
        )

        print(
            f"[SUCCESS] MP3 tayyor: "
            f"{filename} ({size_mb} MB)"
        )

        # =========================================================
        # 11. TELEGRAMGA AUDIO YUBORISH
        # =========================================================

        file = FSInputFile(filename)

        song_title = (
            info.get("title")
            or title
            or clean_query
        )

        caption_artist = artist or "Noma'lum"

        await callback.message.answer_audio(
            audio=file,
            title=song_title[:64],
            performer=caption_artist[:64],
            caption=(
                f"💎 <b>{song_title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎤 {caption_artist}\n"
                f"📦 MP3 • <b>{size_mb} MB</b>\n"
                f"✨ Premium Audio"
            ),
            parse_mode=ParseMode.HTML
        )

        print(
            f"[SUCCESS] To'liq musiqa yuborildi: "
            f"{song_title}"
        )

        await status.delete()

        # =========================================================
        # 12. VAQTINCHALIK FAYLLARNI O'CHIRISH
        # =========================================================

        for p in [filepath, filename]:

            if p and os.path.exists(p):

                try:
                    os.remove(p)
                    print(f"[CLEANUP] O'chirildi: {p}")

                except Exception as e:

                    print(
                        f"[WARN] Faylni o'chirib bo'lmadi: "
                        f"{e}"
                    )

    except Exception as e:

        logging.exception(e)

        print(
            f"[ERROR] ig_audio: {e}"
        )

        err_text = str(e)

        err_text = (
            err_text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        try:

            await status.edit_text(
                f"❌ <b>Xatolik yuz berdi:</b>\n\n"
                f"<code>{err_text[:800]}</code>",
                parse_mode=ParseMode.HTML
            )

        except Exception:

            await callback.message.answer(
                f"❌ Xatolik: {err_text[:800]}"
            )

        # =====================================================
        # FINAL CLEANUP
        # =====================================================

        for p in [filepath, filename]:

            if p and os.path.exists(p):

                try:
                    os.remove(p)

                except Exception:
                    pass


@dp.callback_query(F.data.startswith("dl:"))
async def on_download(callback: CallbackQuery):
    await callback.answer()

    try:
        _, link_id, quality = callback.data.split(":")
    except ValueError:
        await callback.message.answer("❌ Noto'g'ri so'rov")
        return

    url_data = user_links.get(link_id)
    if not url_data:
        await callback.message.answer("❌ Linking eskiribdiku jigar boshqatdan tashlang 😉.")
        return

    url = url_data["url"]
    is_audio = quality == "mp3"
    height = None if is_audio else int(quality)

    status = await callback.message.answer(
        "⏳ Yuklab olinmoqda..." if not is_audio else "⏳ Hozr musiqangni tayyor qilyapman 😁"
    )

    try:
        result = await asyncio.to_thread(download_media, url, height, is_audio)
        filepath = result["filename"]
        size_mb = result["size_mb"]

        # Musiqani aniqlash
        music = url_data.get("music_info")
        if not is_audio and not music:
            await status.edit_text("🎵 Musiqa aniqlanmoqda...")
            music = await recognize_music(filepath)

        # Katta fayl
        if size_mb > 49:
            await status.edit_text("📤 Fayl katta, serverga yuklanmoqda...")
            try:
                link = await asyncio.to_thread(upload_large_file, filepath)
                caption = (
                    f"<b>{result['title']}</b>\n\n"
                    f"🎬 Sifat: <b>{height}p</b>\n"
                    f"📦 Hajmi: <b>{size_mb} MB</b>"
                )
                if music:
                    caption += f"\n\n🎵 <b>{music.get('artist')} — {music.get('title')}</b>"

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📥 Videoni yuklab olish", url=link)]
                ])
                await callback.message.answer(caption, reply_markup=kb, parse_mode=ParseMode.HTML)
                await status.delete()
            except Exception as e:
                logging.exception(e)
                await status.edit_text(f"❌ Fayling vapsheku e 😳({size_mb} MB) a kichikroq tiniqlikni tanla e...")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Kichik fayl
        file = FSInputFile(filepath)

        if is_audio:
            await callback.message.answer_audio(
                audio=file,
                title=result["title"],
                duration=result.get("duration"),
                caption=f"✅ <b>{result['title']}</b>\n🎵 MP3 • {size_mb} MB",
                parse_mode=ParseMode.HTML
            )
        else:
            caption = f"✅ <b>{result['title']}</b>\n🎬 {result.get('height', height)}p • {size_mb} MB"
            if music:
                caption += f"\n\n🎵 <b>{music.get('artist')} — {music.get('title')}</b>"

            await callback.message.answer_video(
                video=file,
                duration=result.get("duration"),
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True
            )

            # Musiqa topilgan bo‘lsa — variantlar
            if music:
                music_text = (
                    f"🎵 <b>{music.get('artist')} — {music.get('title')}</b>\n\n"
                    f"To‘liq qo‘shiqni yuklab olishni xohlaysizmi?"
                )
                buttons = [
                    [InlineKeyboardButton(
                        text="📥 To'liq musiqani yuklab olish (MP3)",
                        callback_data=f"fullsong:{music.get('artist')} - {music.get('title')}"
                    )]
                ]
                await callback.message.answer(
                    music_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    parse_mode=ParseMode.HTML
                )

        if os.path.exists(filepath):
            os.remove(filepath)
        await status.delete()

    except Exception as e:
        logging.exception(e)
        await status.edit_text(f"❌ <code>{e}</code>", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("fullsong:"))
async def on_full_song(callback: CallbackQuery):
    await callback.answer()
    query = callback.data.replace("fullsong:", "").strip()

    status = await callback.message.answer(f"🔍 «{query}» qidirilmoqda...")

    try:
        opts = get_base_opts()
        opts.update({
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": f"{DOWNLOAD_DIR}/%(id)s_song.%(ext)s",
            "default_search": "ytsearch1",
            "noplaylist": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            filename = ydl.prepare_filename(info)
            mp3 = os.path.splitext(filename)[0] + ".mp3"
            if os.path.exists(mp3):
                filename = mp3
            else:
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith(".mp3") and info.get("id", "") in f:
                        filename = os.path.join(DOWNLOAD_DIR, f)
                        break

        if not os.path.exists(filename):
            await status.edit_text("❌ Qo‘shiq topilmadi.")
            return

        size_mb = round(os.path.getsize(filename) / (1024 * 1024), 1)
        file = FSInputFile(filename)

        await callback.message.answer_audio(
            audio=file,
            title=info.get("title", query),
            caption=(
                f"💎 <b>{info.get('title', query)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 MP3 • <b>{size_mb} MB</b>\n"
                f"✨ Premium Audio"
            ),
            parse_mode=ParseMode.HTML
        )
        await status.delete()

        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        logging.exception(e)
        err_text = str(e).replace("<", "&lt;").replace(">", "&gt;")
        await status.edit_text(f"❌ Xatolik:\n<code>{err_text}</code>", parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("findfull:"))
async def on_find_full_music(callback: CallbackQuery):
    """YouTube Shorts uchun to‘liq musiqani topish"""
    await callback.answer()
    link_id = callback.data.split(":")[1]
    url_data = user_links.get(link_id)

    if not url_data:
        await callback.message.answer("❌ Link eskirgan. Qaytadan yuboring.")
        return

    url = url_data["url"]
    status = await callback.message.answer("⏳ Video yuklab olinmoqda va musiqa qidirilmoqda...")

    try:
        # 1. Videoni vaqtincha yuklab olamiz
        result = await asyncio.to_thread(download_media, url, height=None, is_audio=False)
        filepath = result["filename"]

        # 2. Shazam / AudD
        await status.edit_text("🎵 Musiqa aniqlanmoqda...")
        music = await recognize_music(filepath)

        if not music or not music.get("title"):
            await status.edit_text("❌ Musiqani topib bo‘lmadi 😕")
            if os.path.exists(filepath):
                os.remove(filepath)
            return

        query = f"{music.get('artist', '')} - {music.get('title', '')}".strip(" -")
        await status.edit_text(f"🔍 «{query}» qidirilmoqda...")

        # 3. YouTube dan to‘liq qo‘shiqni yuklash
        opts = get_base_opts()
        opts.update({
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": f"{DOWNLOAD_DIR}/%(id)s_song.%(ext)s",
            "default_search": "ytsearch1",
            "noplaylist": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            elif not info:
                raise Exception("Qo‘shiq topilmadi")

            filename = ydl.prepare_filename(info)
            mp3 = os.path.splitext(filename)[0] + ".mp3"
            if os.path.exists(mp3):
                filename = mp3
            else:
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith(".mp3") and (info.get("id", "") in f or "song" in f):
                        filename = os.path.join(DOWNLOAD_DIR, f)
                        break

        if not os.path.exists(filename):
            await status.edit_text("❌ To‘liq qo‘shiq topilmadi.")
            if os.path.exists(filepath):
                os.remove(filepath)
            return

        size_mb = round(os.path.getsize(filename) / (1024 * 1024), 1)
        file = FSInputFile(filename)

        await callback.message.answer_audio(
            audio=file,
            title=info.get("title", query),
            caption=(
                f"💎 <b>{info.get('title', query)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎤 {music.get('artist', 'Noma\'lum')} — {music.get('title', '')}\n"
                f"📦 MP3 • <b>{size_mb} MB</b>\n"
                f"✨ Premium Audio"
            ),
            parse_mode=ParseMode.HTML
        )

        await status.delete()

        # Tozalash
        for p in (filepath, filename):
            if os.path.exists(p):
                os.remove(p)

    except Exception as e:
        logging.exception(e)
        err_text = str(e).replace("<", "&lt;").replace(">", "&gt;")
        await status.edit_text(f"❌ Xatolik:\n<code>{err_text}</code>", parse_mode=ParseMode.HTML)


async def main():
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())