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

DB_PATH = "memelet.db"
MEMES_DIR = "/home/basil/memes/files"
TEMP_FRAMES_DIR = "/home/basil/memes/files/temp/gif_frames"
TEMP_FRAMES_URL = "https://memes.tmn.name/files/temp/gif_frames"

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
    
    media_files = [
        f for f in memes_path.rglob('*') 
        if f.is_file() and f.suffix.lower() in all_extensions
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
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Scan complete. Added {new_count} new file(s)")
    return new_count

def extract_gif_frames(gif_path, max_frames=10):
    """Extract up to max_frames keyframes from GIF"""
    try:
        img = Image.open(gif_path)
        
        # Get total frames
        frame_count = getattr(img, 'n_frames', 1)
        
        # Calculate which frames to extract (evenly distributed)
        if frame_count <= max_frames:
            frame_indices = range(frame_count)
        else:
            step = frame_count / max_frames
            frame_indices = [int(i * step) for i in range(max_frames)]
        
        # Create temp directory for this GIF
        gif_name = Path(gif_path).stem
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
        
        print(f"  ✓ Extracted {len(extracted_frames)} frames")
        return extracted_frames, temp_dir
        
    except Exception as e:
        print(f"  ✗ Frame extraction failed: {e}")
        return [], None

def analyze_meme(file_path, media_type):
    """Send meme to Replicate for analysis"""
    # Convert local file path to public URL
    file_name = Path(file_path).name
    media_url = f"https://memes.tmn.name/files/{file_name}"
    
    temp_dir = None
    
    try:
        if media_type == 'gif':
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
            
            # Consume the iterator (output might be an iterator or list)
            result_list = []
            for item in output:
                result_list.append(item)
                print(f"  → Received chunk: {repr(item)[:100]}")
            
            print(f"  → Total chunks: {len(result_list)}")
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
            
            # Consume the iterator (output might be an iterator or list)
            result_list = []
            for item in output:
                result_list.append(item)
                print(f"  → Received chunk: {repr(item)[:100]}")
        
        print(f"  → Total chunks: {len(result_list)}")
        return result_list
        
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
    
    print(f"\n🔍 Processing: {Path(file_path).name} ({media_type})")
    
    try:
        # Get analysis from Replicate
        result = analyze_meme(file_path, media_type)
        
        # Convert list to string (if needed)
        if isinstance(result, list):
            print(f"  → Result is list with {len(result)} items: {result}")
            result = "".join(str(item) for item in result if item).strip()
        
        print(f"📝 Raw response type: {type(result)}")
        print(f"📝 Raw response length: {len(result) if result else 0}")
        print(f"📝 Raw response: {result[:500] if result else '[EMPTY]'}...")
        
        # Check if result is empty
        if not result or len(result) == 0:
            raise Exception("Empty response from Replicate API")
        
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
        
        # Check again after cleaning
        if not result_clean:
            raise Exception("Empty response after cleaning")
        
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
            data.get('references'),
            data.get('template'),
            data.get('caption'),
            data.get('description'),
            data.get('meaning'),
            meme_id
        ))
        
        conn.commit()
        print(f"✅ Success: {Path(file_path).name}")
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