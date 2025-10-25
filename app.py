#!/usr/bin/env python3
"""
Memelet Web Interface
"""
from flask import Flask, render_template, request
import sqlite3
from pathlib import Path

app = Flask(__name__)

DB_PATH = "memelet.db"
MEMES_URL_BASE = "https://memes.tmn.name/files/"

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """Main page showing all memes"""
    # Get filter parameters
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build the SQL query based on filters
    sql = """
        SELECT id, file_path, status, ref_content, template, 
               caption, description, meaning, created_at
        FROM memes
        WHERE 1=1
    """
    params = []
    
    # Add status filter
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    
    # Add search filter (search in all text fields and file path)
    if search_query:
        sql += """ AND (
            file_path LIKE ? OR
            ref_content LIKE ? OR
            template LIKE ? OR
            caption LIKE ? OR
            description LIKE ? OR
            meaning LIKE ?
        )"""
        search_pattern = f"%{search_query}%"
        params.extend([search_pattern] * 6)
    
    sql += " ORDER BY created_at DESC"
    
    cursor.execute(sql, params)
    
    memes = []
    for row in cursor.fetchall():
        file_name = Path(row['file_path']).name
        memes.append({
            'id': row['id'],
            'image_url': MEMES_URL_BASE + file_name,
            'status': row['status'],
            'ref_content': row['ref_content'],
            'template': row['template'],
            'caption': row['caption'],
            'description': row['description'],
            'meaning': row['meaning'],
            'created_at': row['created_at']
        })
    
    conn.close()
    
    # Get stats (always show all stats, regardless of filters)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) as count FROM memes GROUP BY status")
    stats = {row['status']: row['count'] for row in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) as total FROM memes")
    total = cursor.fetchone()['total']
    conn.close()
    
    return render_template(
        'index.html', 
        memes=memes, 
        stats=stats, 
        total=total,
        search_query=search_query,
        status_filter=status_filter
    )

@app.route('/pic/<int:meme_id>', methods=['GET', 'POST'])
def meme_detail(meme_id):
    """Individual meme page with editing capability"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # Update meme details
        status = request.form.get('status', 'new')
        ref_content = request.form.get('ref_content', '').strip()
        template = request.form.get('template', '').strip()
        caption = request.form.get('caption', '').strip()
        description = request.form.get('description', '').strip()
        meaning = request.form.get('meaning', '').strip()
        
        cursor.execute("""
            UPDATE memes 
            SET status = ?,
                ref_content = ?,
                template = ?,
                caption = ?,
                description = ?,
                meaning = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, ref_content or None, template or None, caption or None, 
              description or None, meaning or None, meme_id))
        
        conn.commit()
    
    # Get meme details
    cursor.execute("""
        SELECT id, file_path, status, ref_content, template, 
               caption, description, meaning, created_at, updated_at
        FROM memes
        WHERE id = ?
    """, (meme_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return "Meme not found", 404
    
    file_name = Path(row['file_path']).name
    meme = {
        'id': row['id'],
        'image_url': MEMES_URL_BASE + file_name,
        'file_name': file_name,
        'status': row['status'],
        'ref_content': row['ref_content'] or '',
        'template': row['template'] or '',
        'caption': row['caption'] or '',
        'description': row['description'] or '',
        'meaning': row['meaning'] or '',
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }
    
    saved = request.method == 'POST'
    
    return render_template('meme_detail.html', meme=meme, saved=saved)

@app.route('/settings')
def settings():
    """Settings page with logs"""
    import os
    
    # Read log file - get last complete entry
    log_path = "/home/basil/memes/logs/scan.log"
    log_content = ""
    
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                content = f.read()
                
                # Split by the separator line
                separator = "================================"
                parts = content.split(separator)
                
                # We want the last complete entry which is:
                # separator + timestamp line + separator + content + completion line
                if len(parts) >= 3:
                    # Get the last 3 parts and reconstruct
                    log_content = separator + parts[-2] + separator + parts[-1]
                else:
                    # If log is too short, show everything
                    log_content = content
                    
        except Exception as e:
            log_content = f"Error reading log: {str(e)}"
    else:
        log_content = "No log file found"
    
    return render_template('settings.html', log_content=log_content)

@app.route('/api/trigger-action', methods=['POST'])
def trigger_action():
    """API endpoint to trigger background actions"""
    import subprocess
    
    data = request.get_json()
    action = data.get('action')
    
    if action == 'scan':
        # Run scan and process in background
        subprocess.Popen(
            ['/home/basil/memes/run_scan.sh'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return {'success': True, 'message': 'Scan started in background!'}
    
    elif action == 'retry_errors':
        # Run retry errors in background
        subprocess.Popen(
            ['bash', '-c', 'cd /home/basil/memes && source venv/bin/activate && source .env && python3 process_memes.py --retry-errors'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return {'success': True, 'message': 'Error reprocessing started in background!'}
    
    return {'success': False, 'message': 'Invalid action'}

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)