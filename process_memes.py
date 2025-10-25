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

DB_PATH = "memelet.db"
MEMES_DIR = "/home/basil/memes/files"

SYSTEM_PROMPT = (
    "You're a meme expert. You're very smart and see meanings between the lines. "
    "You know all famous persons and all characters from every show, movie and game. "
    "Use correct meme names (like Pepe, Wojak, etc.) and media references."
)

USER_PROMPT = (
    'This image is a meme. Analyze it and return json of the following structure: '
    '{references: "Analyze the image to see if it features any famous persons or characters from movies, shows, cartoons or games. If it does, put that information here. If not, omit", '
    'template: "If the images features an established meme character or template (such as \'trollface\', \'wojak\', \'Pepe the Frog\', \'Loss\'), name it here, otherwise omit", '
    'caption: "If the image includes any captions, put them here in the original language, otherwise omit", '
    'description: "Describe the image with its captions (if any) in mind", '
    'meaning: "Explain what this meme means, using information you determined earlier"}'
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
    
    # Get all image files
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    image_files = [
        f for f in memes_path.rglob('*') 
        if f.is_file() and f.suffix.lower() in image_extensions
    ]
    
    new_count = 0
    for image_file in image_files:
        file_path = str(image_file.resolve())
        
        # Check if file already exists in database
        cursor.execute("SELECT id FROM memes WHERE file_path = ?", (file_path,))
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO memes (file_path, status) VALUES (?, 'new')",
                (file_path,)
            )
            new_count += 1
            print(f"➕ Added: {image_file.name}")
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Scan complete. Added {new_count} new file(s)")
    return new_count

def analyze_meme(file_path):
    """Send meme to Replicate for analysis"""
    # Convert local file path to public URL
    file_name = Path(file_path).name
    image_url = f"https://memes.tmn.name/files/{file_name}"
    
    input_data = {
        "prompt": USER_PROMPT,
        "image_input": [image_url],
        "system_prompt": SYSTEM_PROMPT,
        "temperature": 1,
        "top_p": 1,
        "max_completion_tokens": 2048
    }
    
    print(f"  → Sending to Replicate: {image_url}")
    
    output = replicate.run("openai/gpt-4.1-mini", input=input_data)
    
    return output

def process_meme(meme_id, file_path):
    """Process a single meme and update database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print(f"\n🔍 Processing: {Path(file_path).name}")
    
    try:
        # Get analysis from Replicate
        result = analyze_meme(file_path)
        
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
            SELECT id, file_path FROM memes 
            WHERE status IN ('new', 'error')
            ORDER BY created_at
        """)
        print("📋 Processing memes with status: 'new' or 'error'\n")
    else:
        cursor.execute("""
            SELECT id, file_path FROM memes 
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
    
    for meme_id, file_path in pending_memes:
        if process_meme(meme_id, file_path):
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