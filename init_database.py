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
    
    # Create memes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            ref_content TEXT,
            template TEXT,
            caption TEXT,
            description TEXT,
            meaning TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create index on status for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_status ON memes(status)
    """)
    
    conn.commit()
    conn.close()
    
    print(f"Database initialized at: {Path(DB_PATH).resolve()}")
    print(f"Table 'memes' created with fields:")
    print("   - id, file_path, status")
    print("   - ref_content, template, caption, description, meaning")
    print("   - error_message, created_at, updated_at")

if __name__ == "__main__":
    init_database()