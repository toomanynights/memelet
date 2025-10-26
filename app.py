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
    media_filter = request.args.get('media', '')
    page = int(request.args.get('page', 1))
    per_page = 20
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build the SQL query based on filters
    sql = """
        SELECT DISTINCT m.id, m.file_path, m.status, m.media_type, m.ref_content, m.template, 
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
    
    # Add media type filter
    if media_filter:
        sql += " AND m.media_type = ?"
        params.append(media_filter)
    
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
    
    # Get total count for pagination - use a separate count query
    count_sql = """
        SELECT COUNT(DISTINCT m.id)
        FROM memes m
        WHERE 1=1
    """
    count_params = []
    
    # Add status filter to count query
    if status_filter:
        count_sql += " AND m.status = ?"
        count_params.append(status_filter)
    
    # Add tag filter to count query
    if tag_filter:
        count_sql += """ AND m.id IN (
            SELECT meme_id FROM meme_tags WHERE tag_id = ?
        )"""
        count_params.append(tag_filter)
    
    # Add media type filter to count query
    if media_filter:
        count_sql += " AND m.media_type = ?"
        count_params.append(media_filter)
    
    # Add search filter to count query
    if search_query:
        count_sql += """ AND (
            m.file_path LIKE ? OR
            m.ref_content LIKE ? OR
            m.template LIKE ? OR
            m.caption LIKE ? OR
            m.description LIKE ? OR
            m.meaning LIKE ?
        )"""
        search_pattern = f"%{search_query}%"
        count_params.extend([search_pattern] * 6)
    
    cursor.execute(count_sql, count_params)
    total_memes = cursor.fetchone()[0]
    
    # Calculate pagination
    total_pages = (total_memes + per_page - 1) // per_page
    offset = (page - 1) * per_page
    sql += f" LIMIT {per_page} OFFSET {offset}"
    
    cursor.execute(sql, params)
    
    memes = []
    for row in cursor.fetchall():
        file_name = Path(row['file_path']).name
        meme_id = row['id']
        media_type = row['media_type']
        file_path_obj = Path(row['file_path'])
        memes_dir = "/home/basil/memes/files"
        
        # Calculate relative path for proper URLs
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            relative_path_str = relative_path.as_posix()
        except ValueError:
            relative_path_str = file_name
        
        # For videos, use preview GIF from thumbnails directory
        if media_type == 'video':
            video_stem = Path(file_name).stem
            try:
                # Build thumbnail directory relative to memes_dir
                thumbnail_relative = file_path_obj.parent.relative_to(Path(memes_dir)) / 'thumbnails' / f"{video_stem}_preview.gif"
                image_url = MEMES_URL_BASE + thumbnail_relative.as_posix()
            except ValueError:
                # Fallback if path isn't relative to memes_dir
                image_url = MEMES_URL_BASE + f"thumbnails/{video_stem}_preview.gif"
            video_url = MEMES_URL_BASE + relative_path_str
        elif media_type == 'gif':
            # Use the actual GIF (it will animate)
            image_url = MEMES_URL_BASE + relative_path_str
            video_url = MEMES_URL_BASE + relative_path_str
        else:
            image_url = MEMES_URL_BASE + relative_path_str
            video_url = None
        
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
            'image_url': image_url,
            'video_url': video_url,
            'status': row['status'],
            'media_type': media_type,
            'description': row['description'],
            'tags': tags
        })
    
    # Get stats (always show all stats, regardless of filters)
    cursor.execute("SELECT status, COUNT(*) as count FROM memes GROUP BY status")
    stats = {row['status']: row['count'] for row in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) as total FROM memes")
    total = cursor.fetchone()['total']
    
    # Get media type stats
    cursor.execute("SELECT media_type, COUNT(*) as count FROM memes GROUP BY media_type")
    media_stats = {row['media_type']: row['count'] for row in cursor.fetchall()}
    
    # Get all tags with usage count (including tags with 0 usage)
    cursor.execute("""
        SELECT t.id, t.name, t.color, COUNT(mt.meme_id) as usage_count
        FROM tags t
        LEFT JOIN meme_tags mt ON t.id = mt.tag_id
        GROUP BY t.id
        ORDER BY t.name
    """)
    all_tags = [{'id': r['id'], 'name': r['name'], 'color': r['color'], 'count': r['usage_count']} for r in cursor.fetchall()]
    
    conn.close()
    
    return render_template(
        'index.html', 
        memes=memes, 
        stats=stats, 
        total=total,
        media_stats=media_stats,
        all_tags=all_tags,
        search_query=search_query,
        status_filter=status_filter,
        tag_filter=tag_filter,
        media_filter=request.args.get('media', ''),
        page=page,
        total_pages=total_pages,
        show_pagination=total_pages > 1
    )

@app.route('/pic/<int:meme_id>', methods=['GET', 'POST'])
def meme_detail(meme_id):
    """Individual meme page with editing capability"""
    # Get filter parameters for navigation (from GET or POST)
    if request.method == 'POST':
        search_query = request.form.get('search', '')
        status_filter = request.form.get('status_filter', '')
        tag_filter = request.form.get('tag_filter', '')
        media_filter = request.form.get('media_filter', '')
    else:
        search_query = request.args.get('search', '')
        status_filter = request.args.get('status', '')
        tag_filter = request.args.get('tag', '')
        media_filter = request.args.get('media', '')
    
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
        conn.close()
        
        # Redirect back to index with filters preserved
        from flask import redirect, url_for
        redirect_params = []
        if search_query:
            redirect_params.append(f"search={search_query}")
        if status_filter:
            redirect_params.append(f"status={status_filter}")
        if tag_filter:
            redirect_params.append(f"tag={tag_filter}")
        if media_filter:
            redirect_params.append(f"media={media_filter}")
        
        redirect_url = "/?" + "&".join(redirect_params) if redirect_params else "/"
        return redirect(redirect_url)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get meme details
    cursor.execute("""
        SELECT id, file_path, media_type, status, ref_content, template, 
               caption, description, meaning, created_at, updated_at
        FROM memes
        WHERE id = ?
    """, (meme_id,))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return "Meme not found", 404
    
    file_name = Path(row['file_path']).name
    file_path_obj = Path(row['file_path'])
    media_type = row['media_type']
    memes_dir = "/home/basil/memes/files"
    
    # Build proper URLs based on media type
    if media_type == 'video':
        # Use thumbnail for preview, original file for video player
        video_stem = file_path_obj.stem
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            video_url = MEMES_URL_BASE + relative_path.as_posix()
            # Build thumbnail path relative to memes_dir
            thumbnail_relative = relative_path.parent / 'thumbnails' / f"{video_stem}_thumb.jpg"
            image_url = MEMES_URL_BASE + thumbnail_relative.as_posix()
        except ValueError:
            video_url = MEMES_URL_BASE + file_name
            image_url = MEMES_URL_BASE + f"thumbnails/{video_stem}_thumb.jpg"
    else:
        # For images/gifs, calculate relative path for URL
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            image_url = MEMES_URL_BASE + relative_path.as_posix()
        except ValueError:
            image_url = MEMES_URL_BASE + file_name
        video_url = None
    
    meme = {
        'id': row['id'],
        'image_url': image_url,
        'video_url': video_url,
        'file_name': file_name,
        'file_path': row['file_path'],
        'status': row['status'],
        'media_type': media_type,
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
    
    # Get prev/next meme IDs based on current filters
    # Build filtered query
    nav_sql = """
        SELECT DISTINCT m.id, m.created_at
        FROM memes m
        WHERE 1=1
    """
    nav_params = []
    
    if status_filter:
        nav_sql += " AND m.status = ?"
        nav_params.append(status_filter)
    
    if tag_filter:
        nav_sql += """ AND m.id IN (
            SELECT meme_id FROM meme_tags WHERE tag_id = ?
        )"""
        nav_params.append(tag_filter)
    
    if media_filter:
        nav_sql += " AND m.media_type = ?"
        nav_params.append(media_filter)
    
    if search_query:
        nav_sql += """ AND (
            m.file_path LIKE ? OR
            m.ref_content LIKE ? OR
            m.template LIKE ? OR
            m.caption LIKE ? OR
            m.description LIKE ? OR
            m.meaning LIKE ?
        )"""
        search_pattern = f"%{search_query}%"
        nav_params.extend([search_pattern] * 6)
    
    nav_sql += " ORDER BY m.created_at DESC"
    
    cursor.execute(nav_sql, nav_params)
    all_filtered_ids = [r['id'] for r in cursor.fetchall()]
    
    # Find current position and get prev/next
    prev_id = None
    next_id = None
    try:
        current_index = all_filtered_ids.index(meme_id)
        if current_index > 0:
            prev_id = all_filtered_ids[current_index - 1]
        if current_index < len(all_filtered_ids) - 1:
            next_id = all_filtered_ids[current_index + 1]
    except ValueError:
        # Current meme not in filtered list (shouldn't happen but handle it)
        pass
    
    conn.close()
    
    saved = request.method == 'POST'
    
    # Build query string for navigation
    query_params = []
    if search_query:
        query_params.append(f"search={search_query}")
    if status_filter:
        query_params.append(f"status={status_filter}")
    if tag_filter:
        query_params.append(f"tag={tag_filter}")
    if media_filter:
        query_params.append(f"media={media_filter}")
    query_string = "&" + "&".join(query_params) if query_params else ""
    
    return render_template('meme_detail.html', meme=meme, saved=saved, all_tags=all_tags, current_tags=current_tags,
                          prev_id=prev_id, next_id=next_id, query_string=query_string)

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

@app.route('/api/bulk-delete', methods=['POST'])
def bulk_delete():
    """Delete multiple memes"""
    import os
    
    data = request.get_json()
    ids = data.get('ids', [])
    
    if not ids:
        return {'success': False, 'error': 'No IDs provided'}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all file paths
    placeholders = ','.join('?' * len(ids))
    cursor.execute(f"SELECT id, file_path FROM memes WHERE id IN ({placeholders})", ids)
    memes = cursor.fetchall()
    
    # Delete from database
    cursor.execute(f"DELETE FROM memes WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    
    # Delete files from filesystem
    deleted_count = 0
    for meme in memes:
        try:
            if os.path.exists(meme['file_path']):
                os.remove(meme['file_path'])
                deleted_count += 1
        except Exception as e:
            print(f"Warning: Could not delete file {meme['file_path']}: {e}")
    
    return {'success': True, 'deleted': deleted_count}

@app.route('/api/bulk-tags', methods=['POST'])
def bulk_tags():
    """Add or remove tags for multiple memes based on checkbox state"""
    data = request.get_json()
    meme_ids = data.get('meme_ids', [])
    tag_ids = data.get('tag_ids', [])  # Tags to ADD to all
    remove_tag_ids = data.get('remove_tag_ids', [])  # Tags to REMOVE from all
    
    if not meme_ids:
        return {'success': False, 'error': 'No meme IDs provided'}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    added_count = 0
    removed_count = 0
    
    # Add checked tags to all selected memes
    for meme_id in meme_ids:
        for tag_id in tag_ids:
            try:
                cursor.execute("INSERT INTO meme_tags (meme_id, tag_id) VALUES (?, ?)", (meme_id, tag_id))
                added_count += 1
            except sqlite3.IntegrityError:
                # Tag already exists for this meme, skip
                pass
    
    # Remove unchecked tags from all selected memes
    for meme_id in meme_ids:
        for tag_id in remove_tag_ids:
            cursor.execute("DELETE FROM meme_tags WHERE meme_id = ? AND tag_id = ?", (meme_id, tag_id))
            removed_count += 1
    
    conn.commit()
    conn.close()
    
    return {'success': True, 'added': added_count, 'removed': removed_count}

@app.route('/api/bulk-memes-tags', methods=['POST'])
def bulk_memes_tags():
    """Get tags for selected memes - returns full and partial tags"""
    data = request.get_json()
    meme_ids = data.get('meme_ids', [])
    
    if not meme_ids:
        return {'success': False, 'error': 'No meme IDs provided'}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get tags that ALL selected memes have (full)
    placeholders = ','.join('?' * len(meme_ids))
    cursor.execute(f"""
        SELECT tag_id 
        FROM meme_tags 
        WHERE meme_id IN ({placeholders})
        GROUP BY tag_id
        HAVING COUNT(DISTINCT meme_id) = ?
    """, meme_ids + [len(meme_ids)])
    
    full_tag_ids = [row[0] for row in cursor.fetchall()]
    
    # Get tags that SOME (but not all) selected memes have (partial)
    cursor.execute(f"""
        SELECT DISTINCT tag_id 
        FROM meme_tags 
        WHERE meme_id IN ({placeholders}) AND tag_id NOT IN (
            SELECT tag_id 
            FROM meme_tags 
            WHERE meme_id IN ({placeholders})
            GROUP BY tag_id
            HAVING COUNT(DISTINCT meme_id) = ?
        )
    """, meme_ids + meme_ids + [len(meme_ids)])
    
    partial_tag_ids = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        'success': True, 
        'full_tags': full_tag_ids,
        'partial_tags': partial_tag_ids
    }

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
        # Run retry errors in background using dedicated script
        subprocess.Popen(
            ['/home/basil/memes/retry_errors.sh'],
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