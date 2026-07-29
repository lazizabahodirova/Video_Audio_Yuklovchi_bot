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
                    # faqat son bo'lsa
                    duration = int(float(dur_str))
            except (ValueError, TypeError):
                pass

    # 4. Instagram maxsus — ba'zi hollarda "entries" ichida bo'ladi
    if duration <= 0 and info.get("entries"):
        for entry in info["entries"]:
            if entry and entry.get("duration") and entry["duration"] > 0:
                duration = int(entry["duration"])
                break

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
    """Shazam orqali musiqa aniqlash"""
    audio_path = os.path.join(DOWNLOAD_DIR, f"shazam_{uuid.uuid4().hex[:8]}.mp3")
    try:
        # 20 soniyagacha audio ajratamiz
        subprocess.run([
            "ffmpeg", "-y", "-i", filepath,
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            "-t", "20", audio_path
        ], check=True, capture_output=True, timeout=60)

        shazam = Shazam()
        result = await shazam.recognize(audio_path)

        if not result or "track" not in result:
            return None

        track = result["track"]
        return {
            "title": track.get("title", "Noma'lum"),
            "artist": track.get("subtitle", "Noma'lum"),
            "url": track.get("url", ""),
            "image": track.get("images", {}).get("coverarthq") or track.get("images", {}).get("coverart", ""),
            "source": "shazam"
        }
    except Exception as e:
        logging.warning(f"Shazam xatosi: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def recognize_with_audd(filepath: str) -> dict | None:
    """AudD orqali (zaxira)"""
    if not AUDD_API_TOKEN:
        return None

    audio_path = os.path.join(DOWNLOAD_DIR, f"audd_{uuid.uuid4().hex[:8]}.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", filepath,
            "-vn", "-acodec", "libmp3lame", "-q:a", "5",
            "-t", "18", audio_path
        ], check=True, capture_output=True, timeout=60)

        with open(audio_path, "rb") as f:
            resp = requests.post(
                "https://api.audd.io/",
                data={"api_token": AUDD_API_TOKEN, "return": "apple_music,spotify"},
                files={"file": f},
                timeout=30
            )
        data = resp.json()
        if data.get("status") == "success" and data.get("result"):
            r = data["result"]
            return {
                "title": r.get("title"),
                "artist": r.get("artist"),
                "source": "audd"
            }
    except Exception as e:
        logging.warning(f"AudD xatosi: {e}")
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    return None


async def recognize_music(filepath: str) -> dict | None:
    """Avval Shazam, ishlamasa AudD"""
    result = await recognize_with_shazam(filepath)
    if result:
        return result
    return await asyncio.to_thread(recognize_with_audd, filepath)


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


def build_keyboard(link_id: str, size_map: dict, audio_size: int) -> InlineKeyboardMarkup:
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
        kb = build_keyboard(link_id, info["size_map"], info["audio_size"])
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
    link_id = callback.data.split(":")[1]
    url_data = user_links.get(link_id)

    if not url_data:
        await callback.message.answer("Jonim bolam eski linkni ustiga bosma qaytadan yubor linkni 😁")
        return

    url = url_data["url"]
    status = await callback.message.answer("⏳ Musiqa qidirilmoqda...")
    print(f"[INFO] Instagram AUDIO jarayoni boshlandi: {url}")

    try:
        # 1. Videoni yuklab olamiz (audio uchun)
        result = await asyncio.to_thread(download_media, url, height=None, is_audio=False)
        filepath = result["filename"]
        print(f"[INFO] Vaqtinchalik video yuklandi: {filepath}")

        # 2. Shazam / AudD bilan aniqlash
        await status.edit_text("🎵 Musiqa qidirilmoqda...")
        music = await recognize_music(filepath)
        print(f"Natija: {music}")

        if not music:
            # Agar Shazam topmasa, Instagram metadata dan olishga harakat
            music = url_data.get("music_info")
            print(f"[INFO] Metadata dan olingan: {music}")

        if not music or not music.get("title"):
            await status.edit_text("❌ Videoni qayerdan oldinge musiqasini topib bo'lmaydiku 🤔.")
            if os.path.exists(filepath):
                os.remove(filepath)
            return

        query = f"{music.get('artist', '')} - {music.get('title', '')}".strip(" -")
        print(f"[INFO] To‘liq qo‘shiq qidirilmoqda: «{query}»")

        await status.edit_text(f"🔍 «{query}»")

        # 3. YouTube dan to‘liq qo‘shiqni yuklash (mavjud on_full_song logikasi)
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

        print(f"[INFO] YouTube qidiruv: ytsearch1:{query}")

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
            await status.edit_text("❌ Videoni qayerdan olgansan, musiqasini topib bo‘lmadi 😒")
            print("[ERROR] Qo‘shiq fayli topilmadi")
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
        print(f"Mana to'liq musiqang 😁 {info.get('title')}")

        await status.delete()

        # Tozalash
        for p in (filepath, filename):
            if os.path.exists(p):
                os.remove(p)

    except Exception as e:
        logging.exception(e)
        print(f"[ERROR] ig_audio: {e}")
        err_text = str(e).replace("<", "&lt;").replace(">", "&gt;")
        await status.edit_text(f"❌ Xatolik:\n<code>{err_text}</code>", parse_mode=ParseMode.HTML)


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


async def main():
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())