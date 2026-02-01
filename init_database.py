#!/usr/bin/env python3
"""
Initialize the Memelet database
"""
import sqlite3
import re
import subprocess
import shutil
from pathlib import Path
from config import get_db_path, get_install_dir, get_instance_path

# DB_PATH removed - now using dynamic get_db_path() for multi-tenant support

def find_git_executable():
    """
    Find git executable in common locations or PATH.
    Returns full path to git or None if not found.
    """
    # Try common locations first (most reliable)
    common_git_paths = ['/usr/bin/git', '/usr/local/bin/git', '/bin/git']
    for git_path in common_git_paths:
        if Path(git_path).exists():
            return git_path
    
    # Fallback to checking PATH
    git_cmd = shutil.which('git')
    if git_cmd:
        return git_cmd
    
    return None

def validate_version_format(version):
    """
    Validate version format (semver: X.Y.Z, optionally with suffix like -beta).
    Returns True if valid, False otherwise.
    """
    if not version:
        return False
    # Extract base version (without suffix) for validation
    base_version = version.split('-')[0]
    pattern = r'^\d+\.\d+\.\d+$'
    return bool(re.match(pattern, base_version))

def get_version_from_git():
    """
    Detect version from git tags using 'git describe --tags'.
    Works for both exact tag matches and commits after tags.
    Returns version string (e.g., "0.8.7" or "0.8.7-beta") or None if not found.
    """
    try:
        install_dir = Path(get_install_dir())
        git_dir = install_dir / '.git'
        
        if not git_dir.exists():
            return None
        
        # Find git executable
        git_cmd = find_git_executable()
        if not git_cmd:
            return None
        
        # Try to get exact tag match first (if HEAD is on a tag)
        result = subprocess.run(
            [git_cmd, 'describe', '--tags', '--exact-match'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            tag = result.stdout.strip()
            # Remove 'v' prefix if present (e.g., 'v0.8.7' -> '0.8.7')
            version = tag.lstrip('v')
            if validate_version_format(version):
                return version
        
        # If not on exact tag, try to get nearest tag
        result = subprocess.run(
            [git_cmd, 'describe', '--tags'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            describe_output = result.stdout.strip()
            # git describe output format: "v0.8.7" or "v0.8.7-5-gabc1234"
            # Extract the tag part (before first hyphen if it's a commit count)
            tag_part = describe_output.split('-')[0]
            version = tag_part.lstrip('v')
            if validate_version_format(version):
                return version
        
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

def init_database():
    """Create the database and tables if they don't exist"""
    db_path = get_db_path()  # Get path fresh each time for multi-tenant support
    conn = sqlite3.connect(db_path)
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
    if 'file_hash' not in meme_cols:
        cursor.execute("ALTER TABLE memes ADD COLUMN file_hash TEXT")

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

    # Runtime-safe column migrations for album_items
    cursor.execute("PRAGMA table_info(album_items)")
    album_cols = {row[1] for row in cursor.fetchall()}
    if 'file_hash' not in album_cols:
        cursor.execute("ALTER TABLE album_items ADD COLUMN file_hash TEXT")

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
    
    # Initialize version tracking settings
    # current_version: Try to detect from git tags, otherwise None
    cursor.execute("SELECT value FROM settings WHERE key = 'current_version'")
    if cursor.fetchone() is None:
        version = get_version_from_git()
        cursor.execute("INSERT INTO settings (key, value) VALUES ('current_version', ?)", 
                      (version if version else None,))
    
    # current_branch: Default to 'main'
    cursor.execute("SELECT value FROM settings WHERE key = 'current_branch'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('current_branch', 'main')")
    
    # available_version: Initially null, will be updated when checking for updates
    cursor.execute("SELECT value FROM settings WHERE key = 'available_version'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('available_version', NULL)")
    
    # last_update_check: Initially null, will be updated when checking for updates
    cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('last_update_check', NULL)")
    
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
    
    # Get version info for display
    version = get_version_from_git()
    
    print(f"✅ Database initialized at: {Path(db_path).resolve()}")
    print(f"📊 Tables ensured:")
    print("   - memes: id, file_path, file_size, status, media_type, title, ref_content, file_hash, template, caption, description, meaning, error_message, created_at, updated_at")
    print("   - album_items: id, album_id, file_path, display_order, file_size, file_hash")
    print("   - tags: id, name, description, color, parse_from_filename, ai_can_suggest, created_at")
    print("   - meme_tags: meme_id, tag_id")
    print("   - settings: key, value (including version tracking)")
    print("   - users: id, username, password_hash, created_at, updated_at")
    if version:
        print(f"\n📦 Version detected from git tags: {version}")
    else:
        print(f"\n📦 No version detected from git tags (will be set to NULL)")
    print(f"\n🔐 Default login credentials: username='admin', password='admin'")

if __name__ == "__main__":
    init_database()