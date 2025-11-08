#!/usr/bin/env python3
"""
Process memes: scan folder, update database, and generate descriptions
"""
import sys
import json
import sqlite3
import argparse
import replicate
from pathlib import Path
from datetime import datetime
from PIL import Image
import os
import shutil
import cv2
import hashlib

DB_PATH = "memelet.db"
MEMES_DIR = "/home/basil/memes/files"
TEMP_FRAMES_DIR = "/home/basil/memes/files/_system/temp/video_frames"
TEMP_FRAMES_URL = "https://memes.tmn.name/files/_system/temp/video_frames"

SYSTEM_PROMPT = (
    "You're a meme expert. You're very smart and see meanings between the lines. "
    "You know all famous persons and all characters from every show, movie and game. "
    "Use correct meme names (like Pepe, Wojak, etc.) and media references."
)

USER_PROMPT_IMAGE = (
    'This image is a meme. Analyze it and return json of the following structure: '
    '{references: "Analyze the image to see if it features any famous persons or characters from movies, shows, cartoons or games. If it does, put that information here. If not, omit", '
    'template: "If the images features an established meme character or template (such as \'trollface\', \'wojak\', \'Pepe the Frog\', \'Loss\'), name it here, otherwise omit", '
    'caption: "If the image includes any captions, put them here in the original language, otherwise omit", '
    'description: "Describe the image with its captions (if any) in mind", '
    'meaning: "Explain what this meme means, using information you determined earlier"}'
)

USER_PROMPT_GIF = (
    'You are provided with some keyframes of a video. Analyze all that information and return json of the following structure: '
    '{references: "See if it features any famous persons or characters from movies, shows, cartoons or games. If it does, put that information here. If not, omit", '
    'template: "If it features an established meme character or template (such as \'trollface\', \'wojak\', \'Pepe the Frog\', \'Loss\'), name it here, otherwise omit", '
    'caption: "If it includes any captions, put them here in the original language, otherwise omit", '
    'description: "Describe the video, using the frames provided, with its captions (if any) in mind", '
    'meaning: "Explain what this video means, using information you determined earlier"}'
)

USER_PROMPT_ALBUM = (
    'You are provided with multiple images that form a single meme together (an album/sequence). Analyze all images in order and return json of the following structure: '
    '{references: "See if it features any famous persons or characters from movies, shows, cartoons or games. If it does, put that information here. If not, omit", '
    'template: "If it features an established meme format or template (such as \'before and after\', \'expectation vs reality\', \'drake format\', etc.), name it here, otherwise omit", '
    'caption: "If any images include captions, put them ALL here in order, in the original language, otherwise omit", '
    'description: "Describe the entire sequence/album as a cohesive meme, explaining how the images work together", '
    'meaning: "Explain what this meme means as a whole, considering the sequence and relationship between images"}'
)

# Prompt base for tags-from-text only (no images). The actual runnable prompt
# will be augmented with an allowed tag list via _build_prompt_with_tag_suggestions.
USER_PROMPT_TAGS_FROM_TEXT = (
    'You will be provided with textual information about a meme (such as title, caption, description, meaning, references, template). '
    'Based ONLY on this text, return JSON with the following structure: '
    '{tags: "Use ONLY the tags from the provided list below. Provide a comma-separated list of tags that fit. Do NOT invent new tags. If none fit, omit this property."}'
)

def _normalize_for_db(value):
    """Convert AI response values to plain strings acceptable by SQLite."""
    if value is None:
        return None
    # If it's already a string or number, keep as is (cast numbers to str to be consistent)
    if isinstance(value, (str, int, float)):
        return str(value) if not isinstance(value, str) else value
    # Join lists with newlines for readability
    if isinstance(value, list):
        try:
            return "\n".join(str(v) for v in value)
        except Exception:
            return str(value)
    # For dicts or other types, JSON-encode
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)

def get_db_connection():
    """Get database connection"""
    return sqlite3.connect(DB_PATH, timeout=10)

def parse_tags_from_filename(file_path):
    """Parse tags from filename and folder path based on tags that have parse_from_filename enabled.
    Returns list of tag IDs that match the filename or folder path.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all tags that have parse_from_filename enabled
    cursor.execute("""
        SELECT id, name FROM tags 
        WHERE parse_from_filename = 1
    """)
    parseable_tags = cursor.fetchall()
    conn.close()
    
    if not parseable_tags:
        return []
    
    # Get filename/directory name and folder path
    path_obj = Path(file_path)
    
    # Check if this is an album (path points to a directory or ends in 'albums' folder)
    # Use path_obj.is_dir() to check if path exists as directory, but also handle when it doesn't exist yet
    is_album = path_obj.parent.name == 'albums' if path_obj.parts else False
    
    # Get the name part and folder path
    if is_album:
        # For albums, use the directory name
        name_part = path_obj.name.lower()
        folder_path = str(path_obj.parent).lower()
    else:
        # For regular files
        name_part = path_obj.stem.lower()
        folder_path = str(path_obj.parent).lower()
    
    matching_tag_ids = []
    
    for tag_id, tag_name in parseable_tags:
        tag_lower = tag_name.lower()
        
        # Check if tag name appears in filename/directory name (substring search)
        if tag_lower in name_part:
            matching_tag_ids.append(tag_id)
        # Also check if tag name appears in any part of the folder path
        elif tag_lower in folder_path:
            matching_tag_ids.append(tag_id)
    
    return matching_tag_ids

def _load_ai_suggestable_tags():
    """Return list of (name, description) for tags where ai_can_suggest is enabled."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT name, description
        FROM tags
        WHERE ai_can_suggest = 1
        ORDER BY name
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return [(name, description if description is not None else "") for name, description in rows]

def _build_prompt_with_tag_suggestions(base_prompt: str) -> str:
    """Injects a 'tags' property into the prompt spec and appends a list of suggestable tags.

    The inserted property text:
    tags: "Given all information determined about this meme, use ONLY the tags from the provided list below. Provide a comma-separated list of tags that fit. Do NOT invent new tags. If none of the provided tags fit, omit this property."
    """
    # Ensure property is inserted before the final '}' of the prompt spec
    base = base_prompt.rstrip()
    insert_text = (
        'tags: "Given all information determined about this meme, use ONLY the tags from the provided list below. Provide a comma-separated list of tags that fit. Do NOT invent new tags. If none of the provided tags fit, omit this property."'
    )

    if base.endswith('}'):
        # Avoid f-string literal brace issue by concatenation
        prompt_with_property = base[:-1] + ", " + insert_text + "}"
    else:
        # Fallback: just append the instruction plainly
        prompt_with_property = f"{base}\n{insert_text}"

    # Append available tags and descriptions (AI-suggestable only)
    tags = _load_ai_suggestable_tags()
    if tags:
        lines = []
        for name, description in tags:
            if description and description.strip():
                lines.append(f"- {name}: {description}")
            else:
                lines.append(f"- {name}")
        prompt_with_list = (
            prompt_with_property
            + "\n\nAvailable tags (use ONLY from this list):\n"
            + "\n".join(lines)
        )
        return prompt_with_list

    return prompt_with_property

def apply_tags_to_meme(meme_id, tag_ids):
    """Apply given tags to a meme. Skip tags that already exist.
    Returns the count of actually applied (new) tags."""
    if not tag_ids:
        return 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    applied_count = 0
    for tag_id in tag_ids:
        try:
            cursor.execute("""
                INSERT INTO meme_tags (meme_id, tag_id)
                VALUES (?, ?)
            """, (meme_id, tag_id))
            applied_count += 1
        except sqlite3.IntegrityError:
            # Tag already exists for this meme, skip
            pass
    
    conn.commit()
    conn.close()
    return applied_count

def _get_meme_text_blob(meme_id: int) -> str:
    """Collect text fields for a meme and return a consolidated text blob.

    Includes: title, caption, description, meaning, template, ref_content.
    Missing fields are skipped. Each field is labeled to aid the model.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT title, caption, description, meaning, template, ref_content
        FROM memes WHERE id = ?
        """,
        (meme_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return ""
    keys = ["Title", "Caption", "Description", "Meaning", "Template", "References"]
    parts = []
    for label, value in zip(keys, row):
        if value is not None and str(value).strip():
            parts.append(f"{label}: {str(value).strip()}")
    return "\n".join(parts)

def ai_suggest_and_apply_tags_from_text(meme_id: int):
    """Use AI to suggest tags based on meme's text fields only and apply them.

    Returns a tuple (applied_tag_names, unknown_names) for logging purposes.
    """
    text_blob = _get_meme_text_blob(meme_id)
    if not text_blob:
        print(f"  ⚠️ No text content available for meme id={meme_id}; skipping AI tag suggestion")
        return [], []

    prompt = _build_prompt_with_tag_suggestions(USER_PROMPT_TAGS_FROM_TEXT)
    # Provide the textual content explicitly at the end of the prompt
    full_prompt = prompt + "\n\nTEXT INFORMATION:\n" + text_blob

    try:
        input_data = {
            "prompt": full_prompt,
            "system_prompt": SYSTEM_PROMPT,
            "temperature": 1,
            "top_p": 1,
            "max_completion_tokens": 512,
        }
        print("  → Requesting AI tag suggestions from text only")
        output = replicate.run("openai/gpt-4.1-mini", input=input_data)
        if isinstance(output, list):
            output = "".join(output).strip()
        result_clean = str(output).strip()
        if result_clean.startswith("```json"):
            result_clean = result_clean[7:]
        elif result_clean.startswith("```"):
            result_clean = result_clean[3:]
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3]
        result_clean = result_clean.strip()

        data = json.loads(result_clean)
        suggested_value = data.get("tags")
        suggested_names = _parse_ai_suggested_tag_names(suggested_value)
        if not suggested_names:
            print("  ℹ️ AI returned no usable tags from text")
            return [], []
        tag_ids, applied_names, unknown_names = _map_tag_names_to_ids(suggested_names)
        if applied_names:
            actually_applied_count = apply_tags_to_meme(meme_id, tag_ids)
            if actually_applied_count > 0:
                print(f"  🏷️ Applied AI tags from text: {', '.join(applied_names)}")
        if unknown_names:
            print(f"  ⚠️ Ignored unknown/unavailable tags: {', '.join(unknown_names)}")
        return applied_names, unknown_names
    except Exception as e:
        print(f"  ✗ AI tags-from-text failed: {e}")
        return [], []

def scan_tags_for_memes(meme_ids=None, run_path_parse=True, run_ai_text=True, job_id=None):
    """Scan and apply tags for given memes using path parsing and AI-from-text.

    If meme_ids is None, operates on all memes.
    This function does NOT modify meme status or other descriptive fields.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    if meme_ids is None:
        cur.execute("SELECT id, file_path FROM memes ORDER BY id")
        rows = cur.fetchall()
    else:
        placeholders = ",".join(["?"] * len(meme_ids))
        cur.execute(f"SELECT id, file_path FROM memes WHERE id IN ({placeholders}) ORDER BY id", tuple(meme_ids))
        rows = cur.fetchall()
    conn.close()

    if not rows:
        print("✨ No memes to tag-scan")
        return

    print(f"📋 Tag scan: {len(rows)} meme(s) | path_parse={run_path_parse} ai_text={run_ai_text}")
    total_applied = 0
    single = len(rows) == 1
    for meme_id, file_path in rows:
        print(f"\n🔎 Meme id={meme_id}")
        applied_any = False
        if run_path_parse:
            try:
                t_ids = parse_tags_from_filename(file_path)
                if t_ids:
                    actually_applied = apply_tags_to_meme(meme_id, t_ids)
                    total_applied += actually_applied
                    if actually_applied > 0:
                        print(f"  ✓ Path tags applied: {actually_applied}")
                        applied_any = True
                else:
                    print("  ℹ️ No path-derived tags")
            except Exception as e:
                print(f"  ✗ Path tag parse failed: {e}")
        if run_ai_text:
            ai_applied, _ = ai_suggest_and_apply_tags_from_text(meme_id)
            if ai_applied:
                applied_any = True
            total_applied += len(ai_applied)
        # If this was triggered for a single meme and job_id is provided, emit a completion marker
        if single and job_id:
            print(f"TAGSCAN JOB {job_id} COMPLETE id={meme_id} applied={'true' if applied_any else 'false'}")
    print(f"\n✅ Tag scan complete. Total tags applied: {total_applied}")

def _ensure_schema_migrations():
    """Ensure runtime DB has columns needed by this script (idempotent)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(memes)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'file_size' not in cols:
            cursor.execute("ALTER TABLE memes ADD COLUMN file_size INTEGER")
            conn.commit()
        if 'file_hash' not in cols:
            cursor.execute("ALTER TABLE memes ADD COLUMN file_hash TEXT")
            conn.commit()
    except Exception:
        # Non-fatal; continue without blocking scan
        pass
    finally:
        conn.close()

def _get_file_size(path_str: str):
    try:
        return os.path.getsize(path_str)
    except Exception:
        return None

def _get_file_hash(path_str: str):
    """Compute SHA256 hash of file contents."""
    try:
        sha256_hash = hashlib.sha256()
        with open(path_str, "rb") as f:
            # Read in chunks to handle large files
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

def _relocate_by_name_and_hash(filename: str, file_hash: str):
    """Search MEMES_DIR for a file matching filename and hash. 
    First tries to find by filename (fast), then falls back to hash-only search (slower).
    Return absolute path or None."""
    if not file_hash:
        return None
    base = Path(MEMES_DIR)
    if not base.exists():
        return None
    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
    gif_extensions = {'.gif'}
    video_extensions = {'.mp4', '.webm', '.mov', '.avi'}
    all_extensions = image_extensions | gif_extensions | video_extensions
    excluded_dirs = {'thumbnails', 'temp'}
    excluded_suffixes = {'_thumb.jpg', '_preview.gif'}

    # First attempt: Fast search by filename, then verify hash
    candidates = []
    for f in base.rglob(filename):
        try:
            if not f.is_file():
                continue
            if f.suffix.lower() not in all_extensions:
                continue
            if any(part in excluded_dirs for part in f.parts):
                continue
            if any(f.name.endswith(suf) for suf in excluded_suffixes):
                continue
            if _get_file_hash(str(f)) == file_hash:
                candidates.append(str(f.resolve()))
        except Exception:
            continue
    
    if candidates:
        return candidates[0]
    
    # Second attempt: Slower hash-only search (for renamed files)
    # Only search if filename search failed
    print(f"  → Filename search failed, trying hash-only search for {filename}")
    for f in base.rglob('*'):
        try:
            if not f.is_file():
                continue
            if f.suffix.lower() not in all_extensions:
                continue
            if any(part in excluded_dirs for part in f.parts):
                continue
            if any(f.name.endswith(suf) for suf in excluded_suffixes):
                continue
            if _get_file_hash(str(f)) == file_hash:
                print(f"  ✓ Found by hash: {f.name}")
                return str(f.resolve())
        except Exception:
            continue
    
    return None

def _verify_existing_files_and_store_hashes():
    """Before scanning for new memes, verify DB file paths exist; relocate or mark error.
    Returns summary dict with counts.
    """
    _ensure_schema_migrations()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # First, ensure album_items has file_hash column
    try:
        cursor.execute("PRAGMA table_info(album_items)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'file_hash' not in cols:
            cursor.execute("ALTER TABLE album_items ADD COLUMN file_hash TEXT")
            conn.commit()
    except Exception:
        pass
    
    cursor.execute("SELECT id, file_path, media_type, status, file_hash FROM memes")
    rows = cursor.fetchall()
    total = len(rows)
    ok = 0
    hashed = 0
    relocated = 0
    marked_error = 0
    albums = 0
    albums_ok = 0

    for meme_id, file_path, media_type, status, file_hash in rows:
        # Albums: verify items; mark album error if any item missing
        if media_type == 'album':
            albums += 1
            cursor.execute("""
                SELECT file_path, file_hash FROM album_items
                WHERE album_id = ?
                ORDER BY display_order
            """, (meme_id,))
            item_paths_hashes = cursor.fetchall()
            
            # Check each album item
            any_missing = False
            any_relocated = False
            any_hashed = False
            
            for item_path, item_hash in item_paths_hashes:
                if not os.path.exists(item_path):
                    # Missing item → try to relocate
                    filename = Path(item_path).name
                    new_path = _relocate_by_name_and_hash(filename, item_hash) if item_hash else None
                    
                    if new_path:
                        # Update the item path
                        cursor.execute("""
                            UPDATE album_items SET file_path=?
                            WHERE album_id=? AND file_path=?
                        """, (new_path, meme_id, item_path))
                        print(f"↪ Relocated album item (album_id={meme_id}, item={filename}) to {new_path}")
                        any_relocated = True
                        continue
                    else:
                        print(f"✗ Album item missing (album_id={meme_id}, item={filename})")
                        any_missing = True
                        break
                else:
                    # File exists; store hash if missing
                    if item_hash is None:
                        hash_val = _get_file_hash(item_path)
                        if hash_val is not None:
                            cursor.execute("""
                                UPDATE album_items SET file_hash=?
                                WHERE album_id=? AND file_path=?
                            """, (hash_val, meme_id, item_path))
                            print(f"✓ Stored hash for album item (album_id={meme_id})")
                            any_hashed = True
            
            if any_missing:
                cursor.execute("""
                    UPDATE memes
                    SET status='error', error_message=? , updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (f"Album items missing", meme_id))
                marked_error += 1
                print(f"✗ Album marked error (id={meme_id})")
            else:
                ok += 1
                albums_ok += 1
                if any_hashed:
                    hashed += 1
                if any_relocated:
                    relocated += 1
            continue

        # Non-album media
        if os.path.exists(file_path):
            # Store hash if missing
            if file_hash is None:
                hash_val = _get_file_hash(file_path)
                if hash_val is not None:
                    cursor.execute(
                        "UPDATE memes SET file_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (hash_val, meme_id),
                    )
                    hashed += 1
                    print(f"✓ Stored hash for id={meme_id}")
            ok += 1
            continue

            
        # Missing file → attempt relocation if we have hash; if not, mark error
        hash_for_search = file_hash
        if hash_for_search is None:
            print(f"✗ Missing file with no stored hash → marking error (id={meme_id})")
            cursor.execute(
                "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("File missing; no stored hash to relocate", meme_id),
            )
            marked_error += 1
            continue

        filename = Path(file_path).name
        new_path = _relocate_by_name_and_hash(filename, hash_for_search)
        if new_path:
            cursor.execute(
                "UPDATE memes SET file_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_path, meme_id),
            )
            relocated += 1
            print(f"↪ Relocated id={meme_id} to {new_path}")
        else:
            cursor.execute(
                "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("File missing; relocation failed", meme_id),
            )
            marked_error += 1
            print(f"✗ Missing file; relocation failed (id={meme_id})")

    conn.commit()
    conn.close()
    print(f"Pre-scan check: total={total}, ok={ok} (albums={albums}, albums_ok={albums_ok}), hashed={hashed}, relocated={relocated}, errors={marked_error}")
    return {
        'total': total,
        'ok': ok,
        'hashed': hashed,
        'relocated': relocated,
        'errors': marked_error,
        'albums': albums,
        'albums_ok': albums_ok,
    }

def parse_tags_for_all_memes():
    """Parse tags from filenames for all memes in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all memes
    cursor.execute("SELECT id, file_path FROM memes")
    memes = cursor.fetchall()
    
    total_parsed = 0
    total_tags = 0
    
    for meme_id, file_path in memes:
        # Parse tags from filename
        tag_ids = parse_tags_from_filename(file_path)
        
        if tag_ids:
            # Apply tags to meme
            actually_applied = apply_tags_to_meme(meme_id, tag_ids)
            if actually_applied > 0:
                total_parsed += 1
                total_tags += actually_applied
                print(f"  ✓ Applied {actually_applied} tag(s) to meme id={meme_id}")
    
    conn.close()
    print(f"🏷️  Tag parsing complete: {total_parsed} memes tagged with {total_tags} total tags")

def _parse_ai_suggested_tag_names(value):
    """Parse the 'tags' property from AI response into a list of names.
    Accepts comma-separated string or list of strings. Returns cleaned name list.
    """
    if value is None:
        return []
    names = []
    if isinstance(value, str):
        # split by commas and newlines
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
        names = [p.strip().strip('"\'') for p in parts if p.strip()]
    elif isinstance(value, list):
        for item in value:
            if not item:
                continue
            if isinstance(item, str):
                cleaned = item.strip().strip('"\'')
                if cleaned:
                    names.append(cleaned)
            else:
                try:
                    s = str(item).strip()
                    if s:
                        names.append(s)
                except Exception:
                    continue
    else:
        try:
            s = str(value)
            return _parse_ai_suggested_tag_names(s)
        except Exception:
            return []
    # dedupe while preserving order
    seen = set()
    unique = []
    for n in names:
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        unique.append(n)
    return unique

def _map_tag_names_to_ids(tag_names):
    """Map list of tag names to existing tag IDs limited to ai_can_suggest=1.
    Returns (tag_ids, applied_names, unknown_names).
    """
    if not tag_names:
        return [], [], []
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM tags WHERE ai_can_suggest = 1")
    rows = cur.fetchall()
    conn.close()
    name_to_row = {name.lower(): (tid, name) for (tid, name) in [(r[0], r[1]) for r in rows]}
    tag_ids = []
    applied_names = []
    unknown_names = []
    seen_ids = set()
    for raw_name in tag_names:
        key = raw_name.lower().strip()
        if not key:
            continue
        if key in name_to_row:
            tid, canonical = name_to_row[key]
            if tid not in seen_ids:
                tag_ids.append(tid)
                applied_names.append(canonical)
                seen_ids.add(tid)
        else:
            unknown_names.append(raw_name)
    return tag_ids, applied_names, unknown_names

def scan_and_add_new_files():
    """Scan memes directory and add new files to database"""
    # Verify existing records and attempt relocations before scanning for new memes
    _verify_existing_files_and_store_hashes()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    memes_path = Path(MEMES_DIR)
    if not memes_path.exists():
        print(f"❌ Error: Memes directory not found: {MEMES_DIR}")
        return 0
    
    # Get all media files
    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
    gif_extensions = {'.gif'}
    video_extensions = {'.mp4', '.webm', '.mov', '.avi'}
    
    all_extensions = image_extensions | gif_extensions | video_extensions
    
    # Exclude thumbnails and temp directories, and any preview/thumbnail files
    # Also exclude albums directory from regular file scanning
    excluded_dirs = {'thumbnails', '_system', '_albums'}
    excluded_suffixes = {'_thumb.jpg', '_preview.gif'}
    media_files = [
        f for f in memes_path.rglob('*') 
        if f.is_file() 
        and f.suffix.lower() in all_extensions
        and not any(part in excluded_dirs for part in f.parts)
        and not any(f.name.endswith(suffix) for suffix in excluded_suffixes)
    ]
    
    new_count = 0
    for media_file in media_files:
        file_path = str(media_file.resolve())
        extension = media_file.suffix.lower()
        
        # Determine media type
        if extension in gif_extensions:
            media_type = 'gif'
        elif extension in video_extensions:
            media_type = 'video'
        else:
            media_type = 'image'
        
        # Check if file already exists in database
        cursor.execute("SELECT id FROM memes WHERE file_path = ?", (file_path,))
        if cursor.fetchone() is None:
            # Get file hash and check for duplicates by name+hash
            filename = media_file.name
            file_hash = _get_file_hash(file_path)
            
            # Check for duplicates
            if file_hash is not None:
                # Check if another meme has the same hash (content-based duplicate detection)
                cursor.execute(
                    "SELECT id, file_path FROM memes WHERE file_hash = ? LIMIT 1",
                    (file_hash,)
                )
                duplicate = cursor.fetchone()
                
                if duplicate:
                    # Found a duplicate - add it as an error
                    duplicate_id = duplicate[0]
                    duplicate_path = Path(duplicate[1]).name if duplicate[1] else "unknown"
                    cursor.execute(
                        "INSERT INTO memes (file_path, media_type, status, file_hash, error_message) VALUES (?, ?, 'error', ?, ?)",
                        (file_path, media_type, file_hash, f"Duplicate of meme {duplicate_id} ({duplicate_path})")
                    )
                    new_count += 1
                    print(f"⚠️ Duplicate: {filename} (matches meme {duplicate_id}: {duplicate_path})")
                else:
                    # No duplicate - add normally
                    cursor.execute(
                        "INSERT INTO memes (file_path, media_type, status, file_hash) VALUES (?, ?, 'new', ?)",
                        (file_path, media_type, file_hash)
                    )
                    new_count += 1
                    print(f"➕ Added: {filename} ({media_type})")
            else:
                # No hash available - just add normally
                cursor.execute(
                    "INSERT INTO memes (file_path, media_type, status) VALUES (?, ?, 'new')",
                    (file_path, media_type)
                )
                new_count += 1
                print(f"➕ Added: {filename} ({media_type})")
    
    # Scan for albums in /_albums/ directory
    albums_path = memes_path / '_albums'
    if albums_path.exists():
        album_count = scan_albums(cursor, albums_path, image_extensions)
        new_count += album_count
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Scan complete. Added {new_count} new file(s)")
    
    # After scanning, parse tags for all memes
    print("\n🏷️  Parsing tags from filenames...")
    parse_tags_for_all_memes()
    
    return new_count

def scan_albums(cursor, albums_path, image_extensions):
    """Scan albums directory and register albums with their items"""
    new_album_count = 0
    
    # Find all directories in albums folder
    album_dirs = [d for d in albums_path.iterdir() if d.is_dir()]
    
    for album_dir in album_dirs:
        album_path = str(album_dir.resolve())
        album_title = album_dir.name  # Extract folder name as title
        
        # Check if album already exists
        cursor.execute("SELECT id FROM memes WHERE file_path = ? AND media_type = 'album'", (album_path,))
        existing_album = cursor.fetchone()
        
        if existing_album:
            # Album exists, skip
            continue
        
        # Find all image files in the album directory
        album_files = sorted([
            f for f in album_dir.iterdir()
            if f.is_file() and f.suffix.lower() in image_extensions
        ], key=lambda x: x.name)
        
        if not album_files:
            # No images in this directory, skip
            continue
        
        # Register the album in memes table with title
        cursor.execute(
            "INSERT INTO memes (file_path, title, media_type, status) VALUES (?, ?, 'album', 'new')",
            (album_path, album_title)
        )
        album_id = cursor.lastrowid
        
        # Register each file in album_items table
        for order, album_file in enumerate(album_files, start=1):
            file_path = str(album_file.resolve())
            file_hash = _get_file_hash(file_path)
            if file_hash is not None:
                cursor.execute(
                    "INSERT INTO album_items (album_id, file_path, display_order, file_hash) VALUES (?, ?, ?, ?)",
                    (album_id, file_path, order, file_hash)
                )
            else:
                cursor.execute(
                    "INSERT INTO album_items (album_id, file_path, display_order) VALUES (?, ?, ?)",
                    (album_id, file_path, order)
                )
        
        new_album_count += 1
        print(f"➕ Added: {album_title} (album with {len(album_files)} items)")
    
    return new_album_count

def extract_gif_frames(gif_path, max_frames=10):
    """Extract up to max_frames keyframes from GIF"""
    try:
        img = Image.open(gif_path)
        
        # Get total frames
        frame_count = getattr(img, 'n_frames', 1)
        
        gif_name = Path(gif_path).stem
        
        # Calculate which frames to extract (evenly distributed)
        if frame_count <= max_frames:
            frame_indices = range(frame_count)
        else:
            step = frame_count / max_frames
            frame_indices = [int(i * step) for i in range(max_frames)]
        
        # Create temp directory for this GIF
        temp_dir = Path(TEMP_FRAMES_DIR) / gif_name
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        extracted_frames = []
        for idx, frame_num in enumerate(frame_indices):
            img.seek(frame_num)
            frame_path = temp_dir / f"frame_{idx:03d}.jpg"
            img.convert('RGB').save(frame_path, 'JPEG', quality=85)
            
            # Build web-accessible URL
            frame_url = f"{TEMP_FRAMES_URL}/{gif_name}/frame_{idx:03d}.jpg"
            extracted_frames.append(frame_url)
        
        print(f"  ✓ Extracted {len(extracted_frames)} frames from GIF")
        return extracted_frames, temp_dir
        
    except Exception as e:
        print(f"  ✗ GIF frame extraction failed: {e}")
        return [], None

def extract_video_frames(video_path, fps=2, max_frames=20):
    """Extract frames from video at specified FPS, up to max_frames. Also create preview GIF."""
    try:
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            raise Exception("Could not open video file")
        
        # Get video properties
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        if video_fps == 0:
            raise Exception("Could not determine video FPS")
        
        # Calculate frame interval
        frame_interval = int(video_fps / fps)
        
        # Limit total frames
        frames_to_extract = min(max_frames, total_frames // frame_interval)
        
        # Create temp directory for this video
        video_name = Path(video_path).stem
        temp_dir = Path(TEMP_FRAMES_DIR) / video_name
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Create thumbnails directory in _system/thumbnails
        memes_base = Path(MEMES_DIR)
        thumbnails_dir = memes_base / '_system' / 'thumbnails' / Path(video_path).parent.relative_to(memes_base)
        thumbnails_dir.mkdir(parents=True, exist_ok=True)
        
        # Save preview GIF in thumbnails directory
        preview_gif_path = thumbnails_dir / f"{video_name}_preview.gif"
        
        # Save thumbnail in thumbnails directory
        thumbnail_path = thumbnails_dir / f"{video_name}_thumb.jpg"
        
        extracted_frames = []
        preview_frames = []
        frame_count = 0
        saved_count = 0
        
        # For preview GIF: 5 seconds at 10 FPS = 50 frames max
        preview_fps = 10
        preview_duration = 5
        preview_max_frames = preview_fps * preview_duration
        preview_interval = int(video_fps / preview_fps)
        
        while cap.isOpened() and saved_count < frames_to_extract:
            ret, frame = cap.read()
            
            if not ret:
                break
            
            # Save frame at specified interval for AI analysis
            if frame_count % frame_interval == 0:
                frame_path = temp_dir / f"frame_{saved_count:03d}.jpg"
                cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                
                # Save first frame as thumbnail
                if saved_count == 0:
                    cv2.imwrite(str(thumbnail_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    print(f"  ✓ Saved thumbnail: {thumbnail_path.name}")
                
                # Build web-accessible URL
                frame_url = f"{TEMP_FRAMES_URL}/{video_name}/frame_{saved_count:03d}.jpg"
                extracted_frames.append(frame_url)
                saved_count += 1
            
            # Collect frames for preview GIF
            if frame_count % preview_interval == 0 and len(preview_frames) < preview_max_frames:
                # Resize frame for smaller GIF (max width 400px)
                height, width = frame.shape[:2]
                if width > 400:
                    new_width = 400
                    new_height = int(height * (400 / width))
                    frame = cv2.resize(frame, (new_width, new_height))
                
                # Convert BGR to RGB for PIL
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                preview_frames.append(Image.fromarray(frame_rgb))
            
            frame_count += 1
        
        cap.release()
        
        # Create preview GIF from collected frames
        if preview_frames:
            preview_frames[0].save(
                preview_gif_path,
                save_all=True,
                append_images=preview_frames[1:],
                duration=100,  # 100ms per frame = 10 FPS
                loop=0,
                optimize=True
            )
            print(f"  ✓ Created preview GIF: {preview_gif_path.name} ({len(preview_frames)} frames)")
        
        print(f"  ✓ Extracted {len(extracted_frames)} frames from video ({fps} FPS)")
        return extracted_frames, temp_dir
        
    except Exception as e:
        print(f"  ✗ Video frame extraction failed: {e}")
        return [], None

def analyze_meme(file_path, media_type, album_items=None):
    """Send meme to Replicate for analysis"""
    # Get relative path from MEMES_DIR to build proper URL
    file_path_obj = Path(file_path)
    memes_dir_obj = Path(MEMES_DIR)
    
    try:
        relative_path = file_path_obj.relative_to(memes_dir_obj)
        media_url = f"https://memes.tmn.name/files/{relative_path.as_posix()}"
    except ValueError:
        # Fallback if path isn't relative to MEMES_DIR
        file_name = file_path_obj.name
        media_url = f"https://memes.tmn.name/files/{file_name}"
    
    temp_dir = None
    
    try:
        if media_type == 'album':
            # Process album with multiple images
            if not album_items:
                raise Exception("No album items provided")
            
            # Build URLs for all album items
            image_urls = []
            for item_path in album_items:
                try:
                    item_path_obj = Path(item_path)
                    relative_item_path = item_path_obj.relative_to(memes_dir_obj)
                    item_url = f"https://memes.tmn.name/files/{relative_item_path.as_posix()}"
                    image_urls.append(item_url)
                except ValueError:
                    item_url = f"https://memes.tmn.name/files/{item_path_obj.name}"
                    image_urls.append(item_url)
            
            input_data = {
                "prompt": _build_prompt_with_tag_suggestions(USER_PROMPT_ALBUM),
                "image_input": image_urls,
                "system_prompt": SYSTEM_PROMPT,
                "temperature": 1,
                "top_p": 1,
                "max_completion_tokens": 2048
            }
            
            print(f"  → Sending {len(image_urls)} album images to Replicate")
            output = replicate.run("openai/gpt-4.1-mini", input=input_data)
            
        elif media_type == 'gif':
            # Extract frames from GIF
            print(f"  → Extracting frames from GIF: {media_url}")
            frame_urls, temp_dir = extract_gif_frames(file_path, max_frames=10)
            
            if not frame_urls:
                raise Exception("Failed to extract frames from GIF")
            
            # Use image model with multiple frames
            input_data = {
                "prompt": _build_prompt_with_tag_suggestions(USER_PROMPT_GIF),
                "image_input": frame_urls,
                "system_prompt": SYSTEM_PROMPT,
                "temperature": 1,
                "top_p": 1,
                "max_completion_tokens": 2048
            }
            
            print(f"  → Sending {len(frame_urls)} frames to Replicate")
            output = replicate.run("openai/gpt-4.1-mini", input=input_data)
            
        elif media_type == 'video':
            # Extract frames from video
            print(f"  → Extracting frames from video: {media_url}")
            frame_urls, temp_dir = extract_video_frames(file_path, fps=2, max_frames=20)
            
            if not frame_urls:
                raise Exception("Failed to extract frames from video")
            
            # Use same prompt as GIF (they're both videos)
            input_data = {
                "prompt": _build_prompt_with_tag_suggestions(USER_PROMPT_GIF),
                "image_input": frame_urls,
                "system_prompt": SYSTEM_PROMPT,
                "temperature": 1,
                "top_p": 1,
                "max_completion_tokens": 2048
            }
            
            print(f"  → Sending {len(frame_urls)} frames to Replicate")
            output = replicate.run("openai/gpt-4.1-mini", input=input_data)
            
        else:
            # Use image model for static images
            input_data = {
                "prompt": _build_prompt_with_tag_suggestions(USER_PROMPT_IMAGE),
                "image_input": [media_url],
                "system_prompt": SYSTEM_PROMPT,
                "temperature": 1,
                "top_p": 1,
                "max_completion_tokens": 2048
            }
            
            print(f"  → Sending to Replicate (Image): {media_url}")
            output = replicate.run("openai/gpt-4.1-mini", input=input_data)
        
        return output
        
    finally:
        # Cleanup temp frames
        if temp_dir and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
                print(f"  ✓ Cleaned up temp frames")
            except Exception as e:
                print(f"  ✗ Cleanup warning: {e}")

def process_meme(meme_id, file_path, media_type):
    """Process a single meme and update database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Display name based on media type
    if media_type == 'album':
        # Get album title from database
        cursor.execute("SELECT title FROM memes WHERE id = ?", (meme_id,))
        result = cursor.fetchone()
        album_title = result[0] if result and result[0] else Path(file_path).name
        display_name = f"{album_title} (album)"
    else:
        display_name = Path(file_path).name
    
    print(f"\n🔍 Processing: {display_name} ({media_type})")
    
    try:
        # For albums, fetch all items in order
        album_items = None
        if media_type == 'album':
            cursor.execute("""
                SELECT file_path FROM album_items
                WHERE album_id = ?
                ORDER BY display_order
            """, (meme_id,))
            album_items = [row[0] for row in cursor.fetchall()]
            
            if not album_items:
                raise Exception("Album has no items")
            
            print(f"  → Album contains {len(album_items)} images")
        
        # Get analysis from Replicate
        result = analyze_meme(file_path, media_type, album_items=album_items)
        
        # Convert list to string (if needed)
        if isinstance(result, list):
            result = "".join(result).strip()
        
        print(f"📝 Raw response: {result[:200]}...")
        
        # Parse JSON response (handle markdown code blocks)
        result_clean = result.strip()
        
        # Remove markdown code blocks if present
        if result_clean.startswith("```json"):
            result_clean = result_clean[7:]
        elif result_clean.startswith("```"):
            result_clean = result_clean[3:]
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3]
        result_clean = result_clean.strip()
        
        data = json.loads(result_clean)
        
        # Update database with results
        cursor.execute("""
            UPDATE memes 
            SET status = 'done',
                ref_content = ?,
                template = ?,
                caption = ?,
                description = ?,
                meaning = ?,
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            _normalize_for_db(data.get('references')),
            _normalize_for_db(data.get('template')),
            _normalize_for_db(data.get('caption')),
            _normalize_for_db(data.get('description')),
            _normalize_for_db(data.get('meaning')),
            meme_id
        ))
        
        # Commit the primary update before separate tag writes to avoid SQLite write-lock conflicts
        conn.commit()

        # Handle AI-suggested tags
        suggested_value = data.get('tags')
        suggested_names = _parse_ai_suggested_tag_names(suggested_value)
        if suggested_names:
            print(f"🤖 Suggested tags: {', '.join(suggested_names)}")
            tag_ids, applied_names, unknown_names = _map_tag_names_to_ids(suggested_names)
            if applied_names:
                actually_applied = apply_tags_to_meme(meme_id, tag_ids)
                if actually_applied > 0:
                    print(f"🏷️ Applied {actually_applied} tag(s): {', '.join(applied_names)}")
            if unknown_names:
                print(f"⚠️ Ignored unknown/unavailable tags: {', '.join(unknown_names)}")

        # Also apply filename-derived tags for this single meme processing
        filename_tag_ids = parse_tags_from_filename(file_path)
        if filename_tag_ids:
            # Get names for log
            try:
                conn_tags = get_db_connection()
                cur_tags = conn_tags.cursor()
                placeholders = ",".join(["?"] * len(filename_tag_ids))
                cur_tags.execute(
                    f"SELECT name FROM tags WHERE id IN ({placeholders})",
                    tuple(filename_tag_ids)
                )
                names = [row[0] for row in cur_tags.fetchall()]
                conn_tags.close()
            except Exception:
                names = []
            actually_applied = apply_tags_to_meme(meme_id, filename_tag_ids)
            if actually_applied > 0 and names:
                print(f"📄 Applied {actually_applied} filename tag(s): {', '.join(names)}")

        # Final commit (noop if nothing changed)
        conn.commit()
        print(f"✅ Success: {display_name}")
        return True
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON parsing error: {str(e)}"
        print(f"❌ {error_msg}")
        cursor.execute("""
            UPDATE memes 
            SET status = 'error',
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (error_msg, meme_id))
        conn.commit()
        return False
        
    except Exception as e:
        error_msg = f"Processing error: {str(e)}"
        print(f"❌ {error_msg}")
        cursor.execute("""
            UPDATE memes 
            SET status = 'error',
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (error_msg, meme_id))
        conn.commit()
        return False
        
    finally:
        conn.close()

def process_pending_memes(include_errors=False):
    """Process all memes with 'new' status (and optionally 'error' status)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build query based on whether we're including errors
    if include_errors:
        cursor.execute("""
            SELECT id, file_path, media_type FROM memes 
            WHERE status IN ('new', 'error')
            ORDER BY created_at
        """)
        print("📋 Processing memes with status: 'new' or 'error'\n")
    else:
        cursor.execute("""
            SELECT id, file_path, media_type FROM memes 
            WHERE status = 'new'
            ORDER BY created_at
        """)
        print("📋 Processing memes with status: 'new'\n")
    
    pending_memes = cursor.fetchall()
    conn.close()
    
    if not pending_memes:
        print("✨ No memes to process!")
        return
    
    print(f"Found {len(pending_memes)} meme(s) to process\n")
    
    success_count = 0
    error_count = 0
    
    for meme_id, file_path, media_type in pending_memes:
        # If retrying errors, validate file existence and try relocation before processing
        if include_errors:
            # Fetch current status and hash
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            cur2.execute("SELECT status, file_hash FROM memes WHERE id=?", (meme_id,))
            row = cur2.fetchone()
            conn2.close()
            status = row[0] if row else None
            file_hash = row[1] if row else None

            if media_type == 'album':
                # Check album items exist; if OK proceed, else leave as error and continue
                conn3 = get_db_connection()
                c3 = conn3.cursor()
                c3.execute("SELECT file_path FROM album_items WHERE album_id=? ORDER BY display_order", (meme_id,))
                missing = [p for (p,) in c3.fetchall() if not os.path.exists(p)]
                conn3.close()
                if missing:
                    print(f"✗ Album still missing items (id={meme_id}); skipping")
                    continue
            else:
                if not os.path.exists(file_path):
                    filename = Path(file_path).name
                    new_path = _relocate_by_name_and_hash(filename, file_hash) if file_hash else None
                    if new_path:
                        conn_fix = get_db_connection()
                        cur_fix = conn_fix.cursor()
                        cur_fix.execute("UPDATE memes SET file_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_path, meme_id))
                        conn_fix.commit()
                        conn_fix.close()
                        print(f"↪ Relocated id={meme_id} to {new_path}; proceeding to process")
                        file_path = new_path
                    else:
                        print(f"✗ Missing file (id={meme_id}); cannot process")
                        continue

        if process_meme(meme_id, file_path, media_type):
            success_count += 1
        else:
            error_count += 1
    
    print(f"\n{'='*50}")
    print(f"📊 Processing complete:")
    print(f"   ✅ Success: {success_count}")
    print(f"   ❌ Errors: {error_count}")

def show_stats():
    """Show database statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT status, COUNT(*) FROM memes GROUP BY status")
    stats = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM memes")
    total = cursor.fetchone()[0]
    
    conn.close()
    
    print("\n📊 Database Statistics:")
    print(f"   Total memes: {total}")
    for status, count in stats:
        print(f"   - {status}: {count}")
    print()

def main():
    parser = argparse.ArgumentParser(
        description="Memelet: Scan and process memes"
    )
    parser.add_argument(
        '--scan', 
        action='store_true',
        help='Scan memes directory and add new files to database'
    )
    parser.add_argument(
        '--process', 
        action='store_true',
        help='Process all memes with "new" status'
    )
    parser.add_argument(
        '--retry-errors',
        action='store_true',
        help='Process memes with "error" status in addition to "new" ones'
    )
    parser.add_argument(
        '--process-one',
        type=int,
        help='Process a single meme by its id'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show database statistics'
    )
    # Tags-only scan controls
    parser.add_argument(
        '--scan-tags-all',
        action='store_true',
        help='Scan and apply tags (path + AI-from-text) for all memes'
    )
    parser.add_argument(
        '--scan-tags-one',
        type=int,
        help='Scan and apply tags (path + AI-from-text) for a single meme id'
    )
    parser.add_argument(
        '--scan-tags-ids',
        type=str,
        help='Comma-separated list of meme IDs to tag-scan (path + AI-from-text)'
    )
    parser.add_argument(
        '--job-id',
        type=str,
        help='Optional job id for tag-scan log correlation'
    )
    
    args = parser.parse_args()
    
    # If no arguments provided, show help
    if not any(vars(args).values()):
        parser.print_help()
        return
    
    # Check if database exists
    if not Path(DB_PATH).exists():
        print(f"❌ Database not found. Please run init_database.py first!")
        return
    
    # Execute requested actions
    if args.scan_tags_one is not None:
        mid = int(args.scan_tags_one)
        print("================================")
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Starting tags-only scan (id={mid})")
        print("================================")
        scan_tags_for_memes([mid], run_path_parse=True, run_ai_text=True, job_id=args.job_id)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Tags-only scan complete (id={mid})\n")
        return
    if args.scan_tags_ids:
        try:
            ids = [int(x.strip()) for x in args.scan_tags_ids.split(',') if x.strip()]
        except Exception:
            print("❌ Invalid --scan-tags-ids value; expected comma-separated integers")
            return
        if not ids:
            print("✨ No IDs provided for --scan-tags-ids")
            return
        print("================================")
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Starting tags-only scan (ids={ids})")
        print("================================")
        scan_tags_for_memes(ids, run_path_parse=True, run_ai_text=True)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Tags-only scan complete (ids={ids})\n")
        return
    if args.scan_tags_all:
        print("================================")
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Starting tags-only scan for ALL memes")
        print("================================")
        scan_tags_for_memes(None, run_path_parse=True, run_ai_text=True)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Tags-only scan complete (all)\n")
        return
    if args.process_one is not None:
        meme_id = int(args.process_one)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, file_path, media_type FROM memes WHERE id = ?", (meme_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            print(f"❌ Meme id={meme_id} not found")
            return
        _id, file_path, media_type = row
        print("================================")
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Starting single meme processing (id={meme_id})")
        print("================================")
        ok = process_meme(_id, file_path, media_type)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Single meme processing {'succeeded' if ok else 'failed'} (id={meme_id})")
        print("")
        return
    if args.stats:
        show_stats()
    
    if args.scan:
        scan_and_add_new_files()
    
    if args.process or args.retry_errors:
        process_pending_memes(include_errors=args.retry_errors)
    
    # Show stats at the end if we did any processing
    if args.scan or args.process or args.retry_errors:
        show_stats()

if __name__ == "__main__":
    main()