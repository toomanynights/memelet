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

DB_PATH = "memelet.db"
MEMES_DIR = "/home/basil/memes/files"
TEMP_FRAMES_DIR = "/home/basil/memes/files/temp/video_frames"
TEMP_FRAMES_URL = "https://memes.tmn.name/files/temp/video_frames"

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
    return sqlite3.connect(DB_PATH)

def scan_and_add_new_files():
    """Scan memes directory and add new files to database"""
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
    excluded_dirs = {'thumbnails', 'temp', 'albums'}
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
            cursor.execute(
                "INSERT INTO memes (file_path, media_type, status) VALUES (?, ?, 'new')",
                (file_path, media_type)
            )
            new_count += 1
            print(f"➕ Added: {media_file.name} ({media_type})")
    
    # Scan for albums in /albums/ directory
    albums_path = memes_path / 'albums'
    if albums_path.exists():
        album_count = scan_albums(cursor, albums_path, image_extensions)
        new_count += album_count
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Scan complete. Added {new_count} new file(s)")
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
        
        # Create thumbnails directory
        thumbnails_dir = Path(video_path).parent / 'thumbnails'
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
                "prompt": USER_PROMPT_ALBUM,
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
                "prompt": USER_PROMPT_GIF,
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
                "prompt": USER_PROMPT_GIF,
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
                "prompt": USER_PROMPT_IMAGE,
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
        '--stats',
        action='store_true',
        help='Show database statistics'
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