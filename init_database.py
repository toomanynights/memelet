#!/usr/bin/env python3
"""
Initialize the Memelet database
"""
import sqlite3
from pathlib import Path

DB_PATH = "memelet.db"

def init_database():
    """Create the database and tables if they don't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create memes table (base columns)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            file_size INTEGER,
            status TEXT NOT NULL DEFAULT 'new',
            template TEXT,
            caption TEXT,
            description TEXT,
            meaning TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Runtime-safe column migrations for memes
    cursor.execute("PRAGMA table_info(memes)")
    meme_cols = {row[1] for row in cursor.fetchall()}
    if 'media_type' not in meme_cols:
        cursor.execute("ALTER TABLE memes ADD COLUMN media_type TEXT")
    if 'title' not in meme_cols:
        cursor.execute("ALTER TABLE memes ADD COLUMN title TEXT")
    if 'ref_content' not in meme_cols:
        cursor.execute("ALTER TABLE memes ADD COLUMN ref_content TEXT")

    # Create index on status for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_status ON memes(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_media_type ON memes(media_type)
    """)

    # Albums: items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS album_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            display_order INTEGER NOT NULL,
            file_size INTEGER,
            UNIQUE(album_id, display_order)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_album_items_album ON album_items(album_id)
    """)

    # Tags
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            color TEXT NOT NULL,
            parse_from_filename INTEGER NOT NULL DEFAULT 1,
            ai_can_suggest INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Meme-Tags join
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meme_tags (
            meme_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            UNIQUE(meme_id, tag_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_meme_tags_meme ON meme_tags(meme_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_meme_tags_tag ON meme_tags(tag_id)
    """)
    
    # Settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Initialize default agent_form setting if not set
    cursor.execute("SELECT value FROM settings WHERE key = 'agent_form'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('agent_form', 'none')")
    
    # Initialize default replicate_api_key setting if not set
    cursor.execute("SELECT value FROM settings WHERE key = 'replicate_api_key'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('replicate_api_key', '')")
    
    # Initialize default privacy_mode setting if not set (private/public)
    cursor.execute("SELECT value FROM settings WHERE key = 'privacy_mode'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('privacy_mode', 'private')")
    
    # Users table for authentication
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Initialize default admin user if not exists (password: 'admin')
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if cursor.fetchone() is None:
        from werkzeug.security import generate_password_hash
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ('admin', generate_password_hash('admin'))
        )
    
    conn.commit()
    conn.close()
    
    print(f"✅ Database initialized at: {Path(DB_PATH).resolve()}")
    print(f"📊 Tables ensured:")
    print("   - memes: id, file_path, file_size, status, media_type, title, ref_content, template, caption, description, meaning, error_message, created_at, updated_at")
    print("   - album_items: id, album_id, file_path, display_order, file_size")
    print("   - tags: id, name, description, color, parse_from_filename, ai_can_suggest, created_at")
    print("   - meme_tags: meme_id, tag_id")
    print("   - settings: key, value")
    print("   - users: id, username, password_hash, created_at, updated_at")
    print(f"\n🔐 Default login credentials: username='admin', password='admin'")

if __name__ == "__main__":
    init_database()