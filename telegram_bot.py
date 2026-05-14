#!/usr/bin/env python3
"""
Memelet Telegram Bot

Features:
  - Upload image/GIF/video → AI processing → description + tags reply
  - Inline tag selection via keyboard after processing
  - Inline query (@bot keyword) → search memes and return results
  - One-time auth code pairing (generated in Settings)

Run alongside app.py:
  python telegram_bot.py

Required env / .env:
  TELEGRAM_BOT_TOKEN  (or set via Settings UI — bot reads from DB)

The bot reads TELEGRAM_BOT_TOKEN from the DB first, then falls back to the
environment variable, so the Settings UI is the canonical place to set it.
"""

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultGif,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChosenInlineResultHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

# ── project imports ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from config import (
    get_db_path,
    get_memes_dir,
    get_memes_url_base,
    get_script_dir,
    get_venv_dir,
    get_log_dir,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("memelet.bot")

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _setting(key: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_setting(key: str, value) -> None:
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()


# ── auth helpers ───────────────────────────────────────────────────────────────

def _linked_chat_id() -> int | None:
    val = _setting("telegram_linked_chat_id")
    return int(val) if val else None


def _is_authorised(chat_id: int) -> bool:
    return _linked_chat_id() == chat_id


# ── processing helpers ─────────────────────────────────────────────────────────

def _launch_processing(meme_id: int) -> None:
    """Fire process_memes.py --process-one <id> as a detached subprocess."""
    script_dir = get_script_dir()
    venv_dir = get_venv_dir()
    script_path = Path(get_script_dir()).parent / "process_memes.py"
    if not script_path.exists():
        script_path = Path(__file__).parent / "process_memes.py"

    venv_python = Path(venv_dir) / "bin" / "python3"
    python_exec = str(venv_python) if venv_python.exists() else sys.executable

    env = os.environ.copy()
    env["SCRIPT_DIR"] = script_dir
    env["LOG_DIR"] = get_log_dir()
    env["DB_PATH"] = get_db_path()
    env["MEMES_DIR"] = get_memes_dir()
    env["MEMES_URL_BASE"] = get_memes_url_base()
    env["VENV_DIR"] = venv_dir

    subprocess.Popen(
        [python_exec, str(script_path), "--process-one", str(meme_id)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _poll_meme(meme_id: int, timeout: int = 300, interval: float = 3.0):
    """Block until meme status is done/error or timeout. Returns the meme Row."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _db() as conn:
            row = conn.execute("SELECT * FROM memes WHERE id=?", (meme_id,)).fetchone()
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(interval)
    return None


def _get_meme_tags(meme_id: int) -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute(
            "SELECT t.id, t.name, t.color FROM tags t "
            "JOIN meme_tags mt ON mt.tag_id=t.id WHERE mt.meme_id=?",
            (meme_id,),
        ).fetchall()


def _get_all_tags() -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute("SELECT id, name, color FROM tags ORDER BY name").fetchall()


def _set_meme_tags(meme_id: int, tag_ids: list[int]) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM meme_tags WHERE meme_id=?", (meme_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO meme_tags (meme_id, tag_id) VALUES (?,?)",
            [(meme_id, tid) for tid in tag_ids],
        )
        conn.commit()


# ── file saving ────────────────────────────────────────────────────────────────

def _unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


async def _download_tg_file(bot: Bot, file_id: str, dest_path: Path) -> None:
    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(str(dest_path))


def _insert_meme(file_path: Path, media_type: str) -> int:
    import hashlib
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    file_hash = sha.hexdigest()

    with _db() as conn:
        # Duplicate check
        dup = conn.execute(
            "SELECT id FROM memes WHERE file_hash=? LIMIT 1", (file_hash,)
        ).fetchone()
        if dup:
            return dup["id"]

        cursor = conn.execute(
            "INSERT INTO memes (file_path, media_type, status, file_hash) VALUES (?,?,?,?)",
            (str(file_path.resolve()), media_type, "new", file_hash),
        )
        conn.commit()
        return cursor.lastrowid


# ── tag keyboard builder ───────────────────────────────────────────────────────

def _build_tag_keyboard(meme_id: int, selected_ids: set[int]) -> InlineKeyboardMarkup:
    all_tags = _get_all_tags()
    buttons = []
    row = []
    for tag in all_tags:
        tick = "✓ " if tag["id"] in selected_ids else ""
        btn = InlineKeyboardButton(
            f"{tick}{tag['name']}",
            callback_data=f"tag:{meme_id}:{tag['id']}",
        )
        row.append(btn)
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✅ Done", callback_data=f"tagdone:{meme_id}")])
    return InlineKeyboardMarkup(buttons)


# ── search helpers ─────────────────────────────────────────────────────────────

def _search_memes(query: str, limit: int = 50) -> list[sqlite3.Row]:
    words = query.strip().lower().split()
    if not words:
        return []

    base_url = get_memes_url_base().rstrip("/")

    conditions = []
    params = []
    search_cols = "LOWER(COALESCE(description,'')) || ' ' || LOWER(COALESCE(title,'')) || ' ' || LOWER(COALESCE(caption,'')) || ' ' || LOWER(COALESCE(ref_content,''))"
    for w in words:
        conditions.append(f"({search_cols}) LIKE ?")
        params.append(f"%{w}%")

    where = " AND ".join(conditions)
    params.append(limit)

    with _db() as conn:
        rows = conn.execute(
            f"SELECT id, file_path, media_type, description, title FROM memes "
            f"WHERE status='done' AND ({where}) LIMIT ?",
            params,
        ).fetchall()
    return rows


def _public_url(file_path: str) -> str:
    from urllib.parse import quote
    memes_dir = get_memes_dir().rstrip("/").rstrip("\\")
    base_url = get_memes_url_base().rstrip("/")
    try:
        rel = Path(file_path).relative_to(Path(memes_dir))
        return f"{base_url}/{quote(rel.as_posix())}"
    except ValueError:
        return f"{base_url}/{quote(Path(file_path).name)}"


# ── handlers ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args

    if args:
        code = args[0].upper()
        stored = (_setting("telegram_auth_code") or "").upper()
        if stored and code == stored:
            _set_setting("telegram_linked_chat_id", str(chat_id))
            _set_setting("telegram_auth_code", None)
            await update.message.reply_text(
                "✅ Linked! You can now send me images, GIFs, and videos to add to your Memelet library."
            )
            return
        else:
            await update.message.reply_text("❌ Invalid or expired code. Generate a new one in Memelet Settings.")
            return

    if _is_authorised(chat_id):
        await update.message.reply_text(
            "👋 Already linked. Send me a meme to add it to your library, or use me inline with @bot_username <search query>."
        )
    else:
        await update.message.reply_text(
            "🔒 This bot is private. Go to Memelet → Settings → Telegram and generate a link code, "
            "then send it here as:\n\n/start YOUR_CODE"
        )


def _extract_media(msg) -> tuple[str, str, str] | None:
    """Return (file_id, ext, media_type) from a message, or None if no supported media."""
    if msg.photo:
        return msg.photo[-1].file_id, ".jpg", "image"
    if msg.animation:
        return msg.animation.file_id, ".gif", "gif"
    if msg.video:
        return msg.video.file_id, ".mp4", "video"
    if msg.document:
        doc = msg.document
        mime = doc.mime_type or ""
        name = doc.file_name or ""
        if mime.startswith("image/gif") or name.lower().endswith(".gif"):
            return doc.file_id, ".gif", "gif"
        if mime.startswith("video/"):
            return doc.file_id, Path(name or "file.mp4").suffix or ".mp4", "video"
        if mime.startswith("image/"):
            return doc.file_id, Path(name or "file.jpg").suffix or ".jpg", "image"
    return None


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_authorised(chat_id):
        await update.message.reply_text("🔒 Not linked. Use /start YOUR_CODE to authenticate.")
        return

    msg = update.message
    memes_dir = Path(get_memes_dir())
    files_dir = memes_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # Try the message itself, then fall back to the replied-to message
    media = _extract_media(msg)
    source_msg = msg
    if media is None and msg.reply_to_message:
        media = _extract_media(msg.reply_to_message)
        source_msg = msg.reply_to_message

    if media is None:
        await msg.reply_text("❌ Send an image, GIF, or video — or reply to one.")
        return

    file_id, ext, media_type = media

    status_msg = await msg.reply_text("⏳ Downloading…", reply_to_message_id=source_msg.message_id)
    dest = _unique_path(files_dir, f"tg_{int(time.time())}{ext}")

    try:
        await _download_tg_file(context.bot, file_id, dest)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {e}")
        return

    meme_id = _insert_meme(dest, media_type)
    await status_msg.edit_text("🤖 Processing with AI… (this can take up to a minute)")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _launch_processing, meme_id)

    meme = await loop.run_in_executor(None, _poll_meme, meme_id)

    if meme is None:
        await status_msg.edit_text(
            "⚠️ Processing is taking longer than expected. The meme was saved — "
            "check the web interface in a moment."
        )
        return

    if meme["status"] == "error":
        await status_msg.edit_text(
            f"⚠️ Processing failed: {meme['error_message'] or 'unknown error'}.\n"
            "The file was saved and you can retry from the web interface."
        )
        return

    # Build reply text
    lines = []
    if meme["description"]:
        lines.append(f"📝 *Description:* {meme['description']}")
    if meme["meaning"]:
        lines.append(f"💡 *Meaning:* {meme['meaning']}")
    if meme["template"]:
        lines.append(f"🖼 *Template:* {meme['template']}")
    if meme["caption"]:
        lines.append(f"💬 *Caption:* {meme['caption']}")

    suggested = _get_meme_tags(meme_id)
    selected_ids = {t["id"] for t in suggested}

    tag_text = ", ".join(t["name"] for t in suggested) if suggested else "none"
    lines.append(f"\n🏷 *Suggested tags:* {tag_text}")
    lines.append("_Use the buttons below to adjust tags, then tap ✅ Done._")

    await status_msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_build_tag_keyboard(meme_id, selected_ids),
    )


async def handle_tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("tag:"):
        _, meme_id_str, tag_id_str = data.split(":")
        meme_id, tag_id = int(meme_id_str), int(tag_id_str)

        # Read current selection from current keyboard state
        current_markup = query.message.reply_markup
        selected_ids: set[int] = set()
        for row in current_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("tag:"):
                    _, _, bid = btn.callback_data.split(":")
                    if btn.text.startswith("✓"):
                        selected_ids.add(int(bid))

        # Toggle
        if tag_id in selected_ids:
            selected_ids.discard(tag_id)
        else:
            selected_ids.add(tag_id)

        _set_meme_tags(meme_id, list(selected_ids))
        await query.edit_message_reply_markup(
            reply_markup=_build_tag_keyboard(meme_id, selected_ids)
        )

    elif data.startswith("tagdone:"):
        meme_id = int(data.split(":")[1])
        final_tags = _get_meme_tags(meme_id)
        tag_names = ", ".join(t["name"] for t in final_tags) if final_tags else "none"

        # Strip the keyboard, show final confirmation
        text = query.message.text or ""
        # Remove the instruction line
        text = re.sub(r"\n_Use the buttons.*$", "", text, flags=re.S).strip()
        text += f"\n\n✅ *Saved with tags:* {tag_names}"

        await query.edit_message_text(text, parse_mode="Markdown")


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    query_text = (inline_query.query or "").strip()

    if not query_text:
        await inline_query.answer([], cache_time=0)
        return

    rows = _search_memes(query_text, limit=50)
    results = []

    for row in rows:
        url = _public_url(row["file_path"])
        logger.info(f"Inline result URL: {url}")
        media_type = row["media_type"] or "image"
        description = row["description"] or row["title"] or ""
        uid = str(row["id"])

        try:
            if media_type == "gif":
                results.append(
                    InlineQueryResultGif(
                        id=uid,
                        gif_url=url,
                        thumbnail_url=url,
                        title=description[:64] or "GIF",
                    )
                )
            elif media_type == "video":
                results.append(
                    InlineQueryResultVideo(
                        id=uid,
                        video_url=url,
                        mime_type="video/mp4",
                        thumbnail_url=url,
                        title=description[:64] or "Video",
                    )
                )
            else:
                results.append(
                    InlineQueryResultPhoto(
                        id=uid,
                        photo_url=url,
                        thumbnail_url=url,
                        title=description[:64] or "Image",
                        description=description[:128],
                    )
                )
        except Exception as e:
            logger.warning(f"Skipping inline result {uid}: {e}")

    await inline_query.answer(results, cache_time=10)


# ── main ───────────────────────────────────────────────────────────────────────

def get_token() -> str:
    token = _setting("telegram_bot_token") or ""
    if not token.strip():
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return token.strip()


def main() -> None:
    token = get_token()
    if not token:
        logger.error(
            "No Telegram bot token found. Set it in Memelet Settings → Telegram "
            "or via the TELEGRAM_BOT_TOKEN environment variable."
        )
        sys.exit(1)

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.ALL | filters.ANIMATION | filters.VIDEO,
            handle_media,
        )
    )
    # Plain text reply to a media message (for processing from history)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY & ~filters.COMMAND,
            handle_media,
        )
    )
    app.add_handler(CallbackQueryHandler(handle_tag_callback, pattern=r"^tag(done)?:"))
    app.add_handler(InlineQueryHandler(handle_inline_query))

    logger.info("Memelet Telegram bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
