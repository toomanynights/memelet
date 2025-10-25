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
    tag_filter = request.args.get('tag', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build the SQL query based on filters
    sql = """
        SELECT DISTINCT m.id, m.file_path, m.status, m.ref_content, m.template, 
               m.caption, m.description, m.meaning, m.created_at
        FROM memes m
        WHERE 1=1
    """
    params = []
    
    # Add status filter
    if status_filter:
        sql += " AND m.status = ?"
        params.append(status_filter)
    
    # Add tag filter
    if tag_filter:
        sql += """ AND m.id IN (
            SELECT meme_id FROM meme_tags WHERE tag_id = ?
        )"""
        params.append(tag_filter)
    
    # Add search filter (search in all text fields and file path)
    if search_query:
        sql += """ AND (
            m.file_path LIKE ? OR
            m.ref_content LIKE ? OR
            m.template LIKE ? OR
            m.caption LIKE ? OR
            m.description LIKE ? OR
            m.meaning LIKE ?
        )"""
        search_pattern = f"%{search_query}%"
        params.extend([search_pattern] * 6)
    
    sql += " ORDER BY m.created_at DESC"
    
    cursor.execute(sql, params)
    
    memes = []
    for row in cursor.fetchall():
        file_name = Path(row['file_path']).name
        meme_id = row['id']
        
        # Get tags for this meme
        cursor.execute("""
            SELECT t.id, t.name, t.color
            FROM tags t
            JOIN meme_tags mt ON t.id = mt.tag_id
            WHERE mt.meme_id = ?
            ORDER BY t.name
        """, (meme_id,))
        
        tags = [{'id': t['id'], 'name': t['name'], 'color': t['color']} for t in cursor.fetchall()]
        
        memes.append({
            'id': row['id'],
            'image_url': MEMES_URL_BASE + file_name,
            'status': row['status'],
            'description': row['description'],
            'tags': tags
        })
    
    # Get stats (always show all stats, regardless of filters)
    cursor.execute("SELECT status, COUNT(*) as count FROM memes GROUP BY status")
    stats = {row['status']: row['count'] for row in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) as total FROM memes")
    total = cursor.fetchone()['total']
    
    # Get all tags with usage count
    cursor.execute("""
        SELECT t.id, t.name, t.color, COUNT(mt.meme_id) as usage_count
        FROM tags t
        LEFT JOIN meme_tags mt ON t.id = mt.tag_id
        GROUP BY t.id
        HAVING usage_count > 0
        ORDER BY t.name
    """)
    all_tags = [{'id': r['id'], 'name': r['name'], 'color': r['color'], 'count': r['usage_count']} for r in cursor.fetchall()]
    
    conn.close()
    
    return render_template(
        'index.html', 
        memes=memes, 
        stats=stats, 
        total=total,
        all_tags=all_tags,
        search_query=search_query,
        status_filter=status_filter,
        tag_filter=tag_filter
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
        
        # Update tags
        selected_tags = request.form.getlist('tags')
        
        # Remove all existing tags
        cursor.execute("DELETE FROM meme_tags WHERE meme_id = ?", (meme_id,))
        
        # Add selected tags
        for tag_id in selected_tags:
            cursor.execute("""
                INSERT INTO meme_tags (meme_id, tag_id)
                VALUES (?, ?)
            """, (meme_id, int(tag_id)))
        
        conn.commit()
    
    # Get meme details
    cursor.execute("""
        SELECT id, file_path, status, ref_content, template, 
               caption, description, meaning, created_at, updated_at
        FROM memes
        WHERE id = ?
    """, (meme_id,))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return "Meme not found", 404
    
    file_name = Path(row['file_path']).name
    meme = {
        'id': row['id'],
        'image_url': MEMES_URL_BASE + file_name,
        'file_name': file_name,
        'file_path': row['file_path'],
        'status': row['status'],
        'ref_content': row['ref_content'] or '',
        'template': row['template'] or '',
        'caption': row['caption'] or '',
        'description': row['description'] or '',
        'meaning': row['meaning'] or '',
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }
    
    # Get all available tags
    cursor.execute("SELECT id, name, color FROM tags ORDER BY name")
    all_tags = [{'id': r['id'], 'name': r['name'], 'color': r['color']} for r in cursor.fetchall()]
    
    # Get current tags for this meme
    cursor.execute("""
        SELECT t.id, t.name, t.color
        FROM tags t
        JOIN meme_tags mt ON t.id = mt.tag_id
        WHERE mt.meme_id = ?
    """, (meme_id,))
    current_tags = [r['id'] for r in cursor.fetchall()]
    
    conn.close()
    
    saved = request.method == 'POST'
    
    return render_template('meme_detail.html', meme=meme, saved=saved, all_tags=all_tags, current_tags=current_tags)

@app.route('/api/memes/<int:meme_id>', methods=['DELETE'])
def delete_meme(meme_id):
    """Delete a meme from database and filesystem"""
    import os
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get file path before deleting
    cursor.execute("SELECT file_path FROM memes WHERE id = ?", (meme_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return {'success': False, 'error': 'Meme not found'}, 404
    
    file_path = row['file_path']
    
    # Delete from database (CASCADE will handle meme_tags)
    cursor.execute("DELETE FROM memes WHERE id = ?", (meme_id,))
    conn.commit()
    conn.close()
    
    # Delete file from filesystem
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        # File deleted from DB but not filesystem - log but don't fail
        print(f"Warning: Could not delete file {file_path}: {e}")
    
    return {'success': True}

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

@app.route('/tags')
def tags():
    """Tag management page"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT t.*, COUNT(mt.meme_id) as usage_count
        FROM tags t
        LEFT JOIN meme_tags mt ON t.id = mt.tag_id
        GROUP BY t.id
        ORDER BY t.name
    """)
    
    tags_list = []
    for row in cursor.fetchall():
        tags_list.append({
            'id': row['id'],
            'name': row['name'],
            'description': row['description'] or '',
            'color': row['color'],
            'parse_from_filename': bool(row['parse_from_filename']),
            'ai_can_suggest': bool(row['ai_can_suggest']),
            'usage_count': row['usage_count'],
            'created_at': row['created_at']
        })
    
    conn.close()
    
    return render_template('tags.html', tags=tags_list)

@app.route('/api/tags', methods=['POST'])
def create_tag():
    """Create a new tag"""
    data = request.get_json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO tags (name, description, color, parse_from_filename, ai_can_suggest)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data['name'],
            data.get('description', ''),
            data['color'],
            1 if data.get('parse_from_filename', True) else 0,
            1 if data.get('ai_can_suggest', True) else 0
        ))
        conn.commit()
        tag_id = cursor.lastrowid
        conn.close()
        return {'success': True, 'id': tag_id}
    except sqlite3.IntegrityError:
        conn.close()
        return {'success': False, 'error': 'Tag name already exists'}

@app.route('/api/tags/<int:tag_id>', methods=['PUT'])
def update_tag(tag_id):
    """Update a tag"""
    data = request.get_json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE tags
        SET name = ?, description = ?, color = ?, 
            parse_from_filename = ?, ai_can_suggest = ?
        WHERE id = ?
    """, (
        data['name'],
        data.get('description', ''),
        data['color'],
        1 if data.get('parse_from_filename', True) else 0,
        1 if data.get('ai_can_suggest', True) else 0,
        tag_id
    ))
    
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
def delete_tag(tag_id):
    """Delete a tag"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    
    conn.commit()
    conn.close()
    
    return {'success': True}

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)