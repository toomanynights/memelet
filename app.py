#!/usr/bin/env python3
"""
Memelet Web Interface
"""
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from uuid import uuid4
import sqlite3
import re
from pathlib import Path
import os
import hashlib
import subprocess
import sys
import shutil
from datetime import datetime, timedelta
from config import (
    get_db_path,
    get_memes_url_base,
    get_memes_dir,
    get_log_dir,
    get_script_dir,
    get_venv_dir,
    get_host,
    get_port,
    get_disk_quota_mb,
    get_instance_path,
    get_replicate_quota_limit,
    get_replicate_quota_used,
    get_install_dir,
)
import atexit
from init_database import get_version_from_changelog

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Enable template auto-reload
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # 150MB max request size (allows headroom for metadata)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'

# Set session lifetime to 2 weeks
app.permanent_session_lifetime = timedelta(days=14)

# Configure session cookies for mobile browser compatibility
# Mobile browsers (especially Safari on iOS) require explicit cookie attributes
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access (security)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing cross-site navigation
# SESSION_COOKIE_SECURE will be set dynamically based on request context
# This allows it to work in both HTTP (dev) and HTTPS (prod) environments

# Automatic hourly scanning scheduler (only in standalone mode, not multi-tenant)
# Check if we're running in multi-tenant mode by checking for INSTANCE_NAME in config
# Global variable to store the scan function for API access
_hourly_scan_function = None

if 'INSTANCE_NAME' not in app.config:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        
        scheduler = BackgroundScheduler()
        
        def run_hourly_scan():
            """Trigger hourly scan in standalone mode"""
            try:
                script_dir = get_script_dir()
                log_dir = get_log_dir()
                shell_script = os.path.join(script_dir, 'run_scan.sh')
                
                if os.path.exists(shell_script):
                    env = os.environ.copy()
                    env['SCRIPT_DIR'] = script_dir
                    env['LOG_DIR'] = log_dir
                    env['DB_PATH'] = get_db_path()
                    env['MEMES_DIR'] = get_memes_dir()
                    env['MEMES_URL_BASE'] = get_memes_url_base()
                    env['VENV_DIR'] = get_venv_dir()
                    # Ensure PATH includes standard locations for bash
                    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
                    
                    # Use full path to bash to avoid PATH issues in systemd
                    bash_path = '/bin/bash'
                    if not os.path.exists(bash_path):
                        bash_path = '/usr/bin/bash'
                    
                    # Execute with bash explicitly to ensure it runs
                    subprocess.Popen(
                        [bash_path, shell_script],
                        stdout=subprocess.DEVNULL,
                        stderr=open(os.path.join(log_dir, 'scan_errors.log'), 'a'),
                        env=env,
                        start_new_session=True
                    )
                    app.logger.info("Hourly scan triggered")
            except Exception as e:
                app.logger.error(f"Failed to trigger hourly scan: {e}")
        
        # Store function globally for API access
        _hourly_scan_function = run_hourly_scan
        
        # Schedule hourly scan at :00 of every hour
        scheduler.add_job(
            func=run_hourly_scan,
            trigger=CronTrigger.from_crontab('0 * * * *'),
            id='hourly_scan',
            name='Hourly Memelet Scan',
            replace_existing=True
        )
        
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())
        app.logger.info("Automatic hourly scanning enabled (standalone mode)")
    except Exception as e:
        app.logger.error(f"Failed to set up scheduler: {e}")
        import traceback
        traceback.print_exc()
else:
    app.logger.info("Running in multi-tenant mode - scanning managed by Memelord")

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'])
    return None

# Configuration - reads from app.config (multi-tenant) or env vars (standalone)
# Note: Don't cache paths at import time - they need to be dynamic for multi-tenant

# Helper function to ensure version settings exist in database
def _ensure_version_settings(cursor):
    """Ensure version tracking settings exist in the database"""
    # current_version: Read from CHANGELOG.md if available, otherwise None
    cursor.execute("SELECT value FROM settings WHERE key = 'current_version'")
    if cursor.fetchone() is None:
        version = get_version_from_changelog()
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

# Default config
@app.context_processor
def inject_api_key_status():
    """Inject flags describing how the Replicate API key is configured.

    api_key_configured_externally:
        True  -> a non-placeholder key is present in the environment and
                 there is no saved key in the database.
        False -> either no env key, or a DB key exists (BYOK / in-app config).
    """
    # Environment-provided key (ignore known placeholders)
    env_key = os.environ.get('REPLICATE_API_TOKEN')
    is_dummy_env_key = env_key == 'memelord-managed'
    has_real_env_key = bool(env_key and not is_dummy_env_key)

    # Managed-proxy marker (set by a wrapper when using a shared key)
    managed_proxy_flag = os.environ.get('REPLICATE_MANAGED_PROXY') == '1'

    # Application-stored key (set via Settings -> Replicate API Key)
    has_db_key = False
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Ensure settings table exists (in case context processor runs before API endpoints)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Ensure version settings exist (for existing databases)
        _ensure_version_settings(cursor)
        
        cursor.execute("SELECT value FROM settings WHERE key = 'replicate_api_key'")
        row = cursor.fetchone()
        if row and row[0] and row[0].strip():
            has_db_key = True
        conn.commit()
        conn.close()
    except Exception:
        # If settings table is missing or inaccessible, treat as no DB key
        has_db_key = False
        try:
            conn.close()
        except Exception:
            pass

    # We only consider the key "externally configured" when either:
    #   - a real env key exists (standalone / direct), or
    #   - a managed proxy is active (dummy key + management flag),
    # and there is no DB key overriding it.
    api_key_configured_externally = (
        (has_real_env_key or (is_dummy_env_key and managed_proxy_flag))
        and not has_db_key
    )
    
    return dict(api_key_configured_externally=api_key_configured_externally)


def get_memes_url_base_dynamic():
    """Get memes URL base dynamically for multi-tenant support"""
    return get_memes_url_base()

def get_files_dir():
    """Get files directory path dynamically for multi-tenant support"""
    return Path(get_memes_dir())

def get_albums_dir():
    """Get albums directory path dynamically for multi-tenant support"""
    return get_files_dir() / "_albums"

def ensure_directories_exist():
    """Ensure required directories exist"""
    files_dir = get_files_dir()
    albums_dir = get_albums_dir()
    files_dir.mkdir(parents=True, exist_ok=True)
    albums_dir.mkdir(parents=True, exist_ok=True)
    return files_dir, albums_dir

# File type validation
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
GIF_EXTENSIONS = {'.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi'}
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | GIF_EXTENSIONS | VIDEO_EXTENSIONS

# File size limits (in MB)
MAX_IMAGE_SIZE_MB = 10
MAX_GIF_SIZE_MB = 20
MAX_VIDEO_SIZE_MB = 100

# Text field length limits
MAX_TAG_NAME_LENGTH = 25
MAX_TAG_DESCRIPTION_LENGTH = 500
MAX_ALBUM_TITLE_LENGTH = 50
MAX_MEME_CAPTION_LENGTH = 1000
MAX_MEME_DESCRIPTION_LENGTH = 2000
MAX_MEME_MEANING_LENGTH = 2000
MAX_MEME_TEMPLATE_LENGTH = 200
MAX_MEME_REF_CONTENT_LENGTH = 1000

@app.before_request
def set_instance_paths_and_url_root():
    """Set SCRIPT_NAME and APPLICATION_ROOT for URL generation in multi-tenant setup"""
    # Set SCRIPT_NAME for URL generation to include the instance prefix
    # This is crucial for url_for to generate correct URLs like /for/username/login
    if 'INSTANCE_NAME' in app.config:
        instance_name = app.config['INSTANCE_NAME']
        request.environ['SCRIPT_NAME'] = f'/for/{instance_name}'
        app.config['APPLICATION_ROOT'] = f'/for/{instance_name}' # Re-added this for Flask-Login

    # Dynamically set SESSION_COOKIE_SECURE based on request context
    # Check if request is secure (either directly or via proxy header)
    # This allows cookies to work in both HTTP (dev) and HTTPS (prod) environments
    is_secure = (
        request.is_secure or 
        request.headers.get('X-Forwarded-Proto', '').lower() == 'https' or
        os.environ.get('HTTPS', '').lower() == 'true'
    )
    app.config['SESSION_COOKIE_SECURE'] = is_secure

    # Log authentication state for debugging
    if current_user.is_authenticated:
        app.logger.info(f"Auth state: authenticated=True, user_id={current_user.id}")
    else:
        app.logger.info(f"Auth state: authenticated=False, user_id=None")

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon')

@app.route('/files/<path:filename>')
def serve_meme_file(filename):
    """Serve meme files from the memes directory"""
    try:
        files_dir = get_files_dir()
        return send_from_directory(files_dir, filename)
    except FileNotFoundError:
        return "File not found", 404

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    current_year = datetime.now().year
    
    # Handle update request (single-tenant only)
    # In multi-tenant, coordinator intercepts ?v= before it reaches here
    update_version = request.args.get('v')
    if 'INSTANCE_NAME' not in app.config and (update_version or request.args.get('update') == 'dev'):
        # Single-tenant update flow
        branch = get_current_branch()
        # For dev branch, allow update without version (git pull)
        if branch == 'dev' and not update_version:
            target_version = None
        else:
            target_version = update_version
        result = perform_update(target_version=target_version, branch=branch)
        
        if result['success']:
            if result.get('restart_required', False):
                # Show update complete with restart message
                return render_template(
                    'login.html',
                    current_year=current_year,
                    update_complete=True,
                    update_message=result['message'],
                    restart_required=True
                )
            else:
                # Update complete, no restart needed
                flash(result['message'], 'success')
                return render_template('login.html', current_year=current_year)
        else:
            # Update failed
            flash(f'Update failed: {result["message"]}', 'error')
            return render_template('login.html', current_year=current_year)
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter both username and password', 'error')
            return render_template('login.html', username=username, current_year=current_year)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        
        if row and check_password_hash(row['password_hash'], password):
            user = User(row['id'], row['username'])
            login_user(user, remember=True)  # Remember for 2 weeks
            
            # Explicitly mark session as permanent for mobile browser compatibility
            # This ensures the session cookie has the correct expiration
            session.permanent = True
            
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                # In multi-tenant mode, prepend SCRIPT_NAME to the redirect path
                script_name = request.environ.get('SCRIPT_NAME', '')
                if script_name and not next_page.startswith(script_name):
                    next_page = script_name + next_page
                return redirect(next_page)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')
            return render_template('login.html', username=username, current_year=current_year)
    
    return render_template('login.html', current_year=current_year)

@app.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    
    # In public mode, redirect to index instead of login
    if is_public_mode():
        return redirect(url_for('index'))
    
    # In private mode, redirect to login page
    return redirect(url_for('login'))

def get_db_connection():
    """Get database connection with dynamic path for multi-tenant support"""
    db_path = get_db_path()  # Get path fresh each time
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Register a custom Unicode-aware LOWER function for case-insensitive search
    # This replaces SQLite's default LOWER which only works for ASCII
    conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)

    return conn

# Version management helper functions
def get_current_version():
    """Get current version from settings table. Returns version string or None."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure settings table and version settings exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        _ensure_version_settings(cursor)
        conn.commit()
        
        cursor.execute("SELECT value FROM settings WHERE key = 'current_version'")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None

def get_current_branch():
    """Get current branch from settings table. Returns branch string or 'main' as default."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure settings table and version settings exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        _ensure_version_settings(cursor)
        conn.commit()
        
        cursor.execute("SELECT value FROM settings WHERE key = 'current_branch'")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else 'main'
    except Exception:
        return 'main'

def set_current_version(version):
    """Update current_version in settings table. Returns True if successful."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('current_version', ?)",
            (version,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"Error setting current_version: {e}")
        return False

def set_current_branch(branch):
    """Update current_branch in settings table. Returns True if successful."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('current_branch', ?)",
            (branch,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"Error setting current_branch: {e}")
        return False

def set_last_update_check(timestamp=None):
    """Update last_update_check in settings table. Returns True if successful."""
    try:
        if timestamp is None:
            from datetime import datetime
            timestamp = datetime.now().isoformat()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_update_check', ?)",
            (timestamp,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"Error setting last_update_check: {e}")
        return False

def validate_version_format(version):
    """
    Validate version format (semver: X.Y.Z).
    Returns True if valid, False otherwise.
    """
    if not version:
        return False
    import re
    pattern = r'^\d+\.\d+\.\d+$'
    return bool(re.match(pattern, version))

def find_git_executable():
    """
    Find git executable in common locations or PATH.
    Returns full path to git or None if not found.
    """
    # Try common locations first (most reliable)
    common_git_paths = ['/usr/bin/git', '/usr/local/bin/git', '/bin/git']
    for git_path in common_git_paths:
        if Path(git_path).exists():
            app.logger.debug(f"Found git at: {git_path}")
            return git_path
    
    # Fallback to checking PATH
    import shutil
    git_cmd = shutil.which('git')
    if git_cmd:
        app.logger.debug(f"Found git in PATH: {git_cmd}")
        return git_cmd
    
    app.logger.warning("Git executable not found in common locations or PATH")
    return None

def get_dev_commit_info():
    """
    Get current commit hash and check if there are new commits available on dev branch.
    Returns dict with 'current_commit', 'has_new_commits', 'remote_commit' or None if not a git repo.
    """
    try:
        install_dir = Path(get_install_dir())
        git_dir = install_dir / '.git'
        
        if not git_dir.exists():
            app.logger.warning(f"Git directory not found at {git_dir}")
            return None
        
        # Find git executable
        git_cmd = find_git_executable()
        if not git_cmd:
            app.logger.warning("Git command not found")
            return None
        
        # Check if git command works
        try:
            check_git = subprocess.run(
                [git_cmd, '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if check_git.returncode != 0:
                app.logger.warning("Git command not available (git --version failed)")
                return None
        except FileNotFoundError:
            app.logger.warning("Git command not found")
            return None
        
        # Get current commit hash
        result = subprocess.run(
            [git_cmd, 'rev-parse', 'HEAD'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            app.logger.warning(f"Failed to get current commit (returncode {result.returncode}): {result.stderr}")
            return None
        
        current_commit = result.stdout.strip()
        if not current_commit:
            app.logger.warning("Empty commit hash returned")
            return None
        
        # Fetch latest (don't pull, just check) - ignore errors (network might be down)
        fetch_result = subprocess.run(
            [git_cmd, 'fetch', 'origin', 'dev'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30
        )
        if fetch_result.returncode != 0:
            app.logger.debug(f"Git fetch failed (may be offline): {fetch_result.stderr}")
            # Still return current commit even if fetch fails
            return {
                'current_commit': current_commit[:8] if current_commit else None,
                'has_new_commits': False,
                'remote_commit': None
            }
        
        # Get remote commit hash
        result = subprocess.run(
            [git_cmd, 'rev-parse', 'origin/dev'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            app.logger.debug(f"Failed to get remote commit: {result.stderr}")
            return {
                'current_commit': current_commit[:8] if current_commit else None,
                'has_new_commits': False,
                'remote_commit': None
            }
        
        remote_commit = result.stdout.strip()
        has_new_commits = current_commit != remote_commit
        
        return {
            'current_commit': current_commit[:8] if current_commit else None,
            'has_new_commits': has_new_commits,
            'remote_commit': remote_commit[:8] if remote_commit else None
        }
    except subprocess.TimeoutExpired:
        app.logger.warning("Timeout getting dev commit info")
        return None
    except Exception as e:
        app.logger.warning(f"Error getting dev commit info: {e}", exc_info=True)
        return None

def get_available_version():
    """
    Get available version from GitHub API (single-tenant) or config.json/env var (multi-tenant).
    For dev branch, returns commit info string (e.g., "commit: abc1234").
    Returns version string (e.g., "1.2.3") or None if not available.
    """
    # Check if we're in multi-tenant mode
    if 'INSTANCE_NAME' in app.config:
        # Multi-tenant: read from config.json first (like git_branch), then env var
        try:
            instance_path = Path(get_instance_path())
            config_file = instance_path / 'config.json'
            if config_file.exists():
                import json
                with open(config_file, 'r') as f:
                    instance_config = json.load(f)
                    available_version = instance_config.get('available_version')
                    if available_version and validate_version_format(available_version):
                        return available_version
        except Exception as e:
            app.logger.warning(f"Could not read available_version from config.json: {e}")
        
        # Fallback to environment variable (set by wrapper)
        available_version = os.environ.get('AVAILABLE_VERSION')
        if available_version and validate_version_format(available_version):
            return available_version
        return None
    else:
        # Single-tenant: check current branch first
        current_branch = get_current_branch()
        
        # Dev branch: return commit info instead of version
        if current_branch == 'dev':
            commit_info = get_dev_commit_info()
            if commit_info and commit_info.get('current_commit'):
                # Return commit hash as "available version" for display
                # The check_for_updates function will handle commit comparison
                return f"commit:{commit_info['current_commit']}"
            # Not a git repo or failed - return None (will show "Checking..." or "None")
            return None
        
        # For other branches, check GitHub API for latest release
        # Filter tags by branch name pattern (e.g., beta releases tagged as "0.8.1-beta")
        try:
            import requests
            
            # Get GitHub repo from env or use default
            github_repo = os.environ.get('GITHUB_REPO', 'toomanynights/memelet')
            
            # Get all tags and filter by branch
            tags_url = f'https://api.github.com/repos/{github_repo}/tags'
            tags_response = requests.get(tags_url, timeout=5)
            if tags_response.status_code == 200:
                tags = tags_response.json()
                if tags:
                    # Filter tags: prefer tags that match branch name pattern
                    # e.g., for "beta" branch, prefer tags like "0.8.1-beta" or tags on beta branch
                    # For "main" branch, prefer tags like "0.8.1" (no suffix)
                    branch_suffix = f'-{current_branch}' if current_branch != 'main' else ''
                    
                    # First, try to find tags matching branch pattern
                    for tag in tags:
                        tag_name = tag.get('name', '').lstrip('v')
                        # Check if tag matches branch pattern
                        if current_branch == 'main':
                            # Main branch: prefer tags without suffix (e.g., "0.8.1", not "0.8.1-beta")
                            if validate_version_format(tag_name) and '-' not in tag_name:
                                return tag_name
                        else:
                            # Other branches: prefer tags with branch suffix (e.g., "0.8.1-beta")
                            if tag_name.endswith(branch_suffix):
                                version = tag_name[:-len(branch_suffix)]
                                if validate_version_format(version):
                                    return version
                            # Fallback: if no branch-specific tag, check if tag exists on this branch
                            # (This requires git, so we'll just use the first valid tag as fallback)
                    
                    # Fallback: use first valid semver tag
                    for tag in tags:
                        tag_name = tag.get('name', '').lstrip('v')
                        if validate_version_format(tag_name):
                            return tag_name
            
            # Also try releases endpoint (but releases are global, not branch-specific)
            api_url = f'https://api.github.com/repos/{github_repo}/releases/latest'
            response = requests.get(api_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                tag_name = data.get('tag_name', '').lstrip('v')
                if validate_version_format(tag_name):
                    return tag_name
        except requests.exceptions.RequestException as e:
            app.logger.warning(f"Error checking GitHub for available version: {e}")
        except Exception as e:
            app.logger.warning(f"Error getting available version: {e}")
        
        return None

def check_for_updates():
    """
    Check if an update is available by comparing current version with available version.
    For dev branch, checks if there are new commits available.
    Returns dict with:
    - update_available: bool
    - current_version: str or None
    - available_version: str or None
    - needs_update: bool (True if available_version > current_version or new commits available)
    """
    current = get_current_version()
    current_branch = get_current_branch()
    available = get_available_version()
    
    result = {
        'update_available': False,
        'current_version': current,
        'available_version': available,
        'needs_update': False
    }
    
    # Special handling for dev branch (commit-based updates)
    if current_branch == 'dev':
        commit_info = get_dev_commit_info()
        if commit_info:
            # Format available version for display
            if commit_info.get('has_new_commits'):
                result['update_available'] = True
                result['needs_update'] = True
                # Show remote commit as "available"
                if commit_info.get('remote_commit'):
                    result['available_version'] = f"commit:{commit_info['remote_commit']}"
            else:
                # No new commits, but still show current commit
                result['update_available'] = False
                result['needs_update'] = False
                if commit_info.get('current_commit'):
                    result['available_version'] = f"commit:{commit_info['current_commit']}"
        return result
    
    # For other branches, compare versions
    if not available:
        return result
    
    result['update_available'] = True
    
    if not current:
        # No current version, assume update is needed
        result['needs_update'] = True
        return result
    
    # Handle commit-based "available version" (shouldn't happen for non-dev, but handle gracefully)
    if available.startswith('commit:'):
        # This shouldn't happen for non-dev branches, but if it does, don't show update
        result['update_available'] = False
        return result
    
    # Compare versions (simple string comparison works for semver)
    try:
        current_parts = [int(x) for x in current.split('.')]
        available_parts = [int(x) for x in available.split('.')]
        
        if available_parts > current_parts:
            result['needs_update'] = True
    except (ValueError, AttributeError):
        # If version comparison fails, assume update is needed if versions differ
        if available != current:
            result['needs_update'] = True
    
    return result

def perform_update(target_version, branch=None, install_dir=None, github_repo=None):
    """
    Perform version update for single-tenant instances.
    
    Args:
        target_version: Version to update to (e.g., "1.2.3") or None for dev branch
        branch: Current branch (defaults to reading from database)
        install_dir: Installation directory (defaults to get_install_dir())
        github_repo: GitHub repository (defaults to env var or 'toomanynights/memelet')
    
    Returns:
        dict with 'success' (bool), 'message' (str), and optionally 'restart_required' (bool)
    """
    # Only allow in single-tenant mode
    if 'INSTANCE_NAME' in app.config:
        return {
            'success': False,
            'message': 'Updates are handled by memelord in multi-tenant mode'
        }
    
    try:
        # Get defaults
        if install_dir is None:
            install_dir = Path(get_install_dir())
        else:
            install_dir = Path(install_dir)
        
        if github_repo is None:
            github_repo = os.environ.get('GITHUB_REPO', 'toomanynights/memelet')
        
        if branch is None:
            branch = get_current_branch()
        
        app.logger.info(f"Starting update: branch={branch}, target_version={target_version}")
        
        # Handle dev branch: git pull
        if branch == 'dev':
            if not target_version:
                # Dev branch update: pull latest commits
                git_dir = install_dir / '.git'
                if not git_dir.exists():
                    return {
                        'success': False,
                        'message': 'Install directory is not a git repository. Cannot update dev branch.'
                    }
                
                # Find git executable
                git_cmd = find_git_executable()
                if not git_cmd:
                    return {
                        'success': False,
                        'message': 'Git command not found. Please ensure git is installed.'
                    }
                
                # Get current commit hash
                result = subprocess.run(
                    [git_cmd, 'rev-parse', 'HEAD'],
                    cwd=install_dir,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                old_commit = result.stdout.strip() if result.returncode == 0 else None
                
                # Fetch and pull
                app.logger.info("Fetching latest changes...")
                result = subprocess.run(
                    [git_cmd, 'fetch', 'origin'],
                    cwd=install_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode != 0:
                    return {
                        'success': False,
                        'message': f'Git fetch failed: {result.stderr}'
                    }
                
                app.logger.info("Pulling latest commits...")
                result = subprocess.run(
                    [git_cmd, 'pull', 'origin', 'dev'],
                    cwd=install_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode != 0:
                    return {
                        'success': False,
                        'message': f'Git pull failed: {result.stderr}'
                    }
                
                # Get new commit hash
                result = subprocess.run(
                    [git_cmd, 'rev-parse', 'HEAD'],
                    cwd=install_dir,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                new_commit = result.stdout.strip() if result.returncode == 0 else None
                
                if old_commit and new_commit and old_commit == new_commit:
                    return {
                        'success': True,
                        'message': 'Already on latest commit',
                        'restart_required': False
                    }
                
                # Update version from CHANGELOG.md if available
                from init_database import get_version_from_changelog
                new_version = get_version_from_changelog()
                if new_version:
                    set_current_version(new_version)
                
                return {
                    'success': True,
                    'message': f'Successfully updated dev branch (commit: {new_commit[:8] if new_commit else "unknown"})',
                    'restart_required': True
                }
            else:
                return {
                    'success': False,
                    'message': 'Cannot specify target version for dev branch. Use git pull instead.'
                }
        
        # Other branches: download specific version
        if not target_version:
            return {
                'success': False,
                'message': 'Target version is required for non-dev branches'
            }
        
        # Validate version format
        if not validate_version_format(target_version):
            return {
                'success': False,
                'message': f'Invalid version format: {target_version}'
            }
        
        # Check if already on this version
        current_version = get_current_version()
        if current_version == target_version:
            return {
                'success': True,
                'message': f'Already on version {target_version}',
                'restart_required': False
            }
        
        app.logger.info(f"Downloading version {target_version} from {github_repo}")
        
        # Find git executable
        git_cmd = find_git_executable()
        if not git_cmd:
            return {
                'success': False,
                'message': 'Git command not found. Please ensure git is installed.'
            }
        
        # Create temporary directory for download
        temp_dir = install_dir.parent / f'memelet-{target_version}-temp'
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Clone the repository at the specific tag/version
            tag = f'v{target_version}' if not target_version.startswith('v') else target_version
            clone_cmd = [
                git_cmd, 'clone',
                '--depth', '1',
                '--branch', tag,
                f'https://github.com/{github_repo}.git',
                str(temp_dir)
            ]
            
            result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                # Try without branch (might be a tag)
                clone_cmd = [
                    git_cmd, 'clone',
                    '--depth', '1',
                    f'https://github.com/{github_repo}.git',
                    str(temp_dir)
                ]
                result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    return {
                        'success': False,
                        'message': f'Git clone failed: {result.stderr}'
                    }
                
                # Checkout the specific tag
                checkout_cmd = [git_cmd, 'checkout', tag]
                result = subprocess.run(checkout_cmd, cwd=temp_dir, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    return {
                        'success': False,
                        'message': f'Git checkout failed: {result.stderr}'
                    }
            
            # Backup current installation (except user data)
            backup_dir = install_dir.parent / f'memelet-backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # Files/directories to preserve (user data)
            preserve = ['files', 'logs', 'memelet.db', 'venv', '.env', 'config.json', '.git']
            
            # Copy files to backup (excluding preserved items)
            for item in install_dir.iterdir():
                if item.name not in preserve:
                    if item.is_dir():
                        shutil.copytree(item, backup_dir / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, backup_dir / item.name)
            
            # Copy new version files (excluding preserved items from new version)
            new_files = temp_dir / 'memelet' if (temp_dir / 'memelet').exists() else temp_dir
            for item in new_files.iterdir():
                if item.name not in preserve:
                    dest = install_dir / item.name
                    if item.is_dir():
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            
            # Clean up temp directory
            shutil.rmtree(temp_dir)
            
            # Update version in database
            if not set_current_version(target_version):
                app.logger.warning("Failed to update version in database, but update completed")
            
            return {
                'success': True,
                'message': f'Successfully updated to version {target_version}',
                'restart_required': True
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'message': 'Update timed out. Please try again.'
            }
        except Exception as e:
            app.logger.error(f"Error during update: {e}")
            return {
                'success': False,
                'message': f'Update failed: {str(e)}'
            }
    
    except Exception as e:
        app.logger.error(f"Error in perform_update: {e}")
        return {
            'success': False,
            'message': f'Update error: {str(e)}'
        }

def is_public_mode():
    """Check if site is in public mode"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'privacy_mode'")
        row = cursor.fetchone()
        conn.close()
        return row and row['value'] == 'public'
    except Exception:
        return False

def login_required_unless_public(f):
    """Decorator that requires login only if not in public mode"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_public_mode() and not current_user.is_authenticated:
            return login_manager.unauthorized()
        return f(*args, **kwargs)
    return decorated_function

def get_clippy_agent():
    """Get current Clippy agent selection from settings"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure settings table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Ensure default value exists
        cursor.execute("SELECT value FROM settings WHERE key = 'agent_form'")
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO settings (key, value) VALUES ('agent_form', 'none')")
            conn.commit()
            agent_form = 'none'
        else:
            agent_form = row[0]
        conn.close()
        return agent_form
    except Exception:
        return 'none'

@app.route('/')
@login_required_unless_public
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
        SELECT DISTINCT m.id, m.file_path, m.title, m.status, m.media_type, m.ref_content, m.template, 
               m.caption, m.description, m.meaning, m.error_message, m.created_at
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
    
    # Add search filter (search in all text fields, title and file path)
    # Use custom LOWER function for Unicode-aware case-insensitive search
    # Support:
    #   - word-based search: all words must appear (in any order)
    #   - phrase search: anything in "double quotes" must appear as a substring
    if search_query:
        # Extract quoted phrases: "exact phrase"
        phrases = re.findall(r'"([^"]+)"', search_query)
        # Remove quoted phrases from the string, then split remaining text into words
        remaining = re.sub(r'"[^"]+"', " ", search_query)
        words = [w for w in remaining.split() if w]

        # Terms to require, all combined with AND
        # Each term is (type, text) where type is 'phrase' or 'word'
        terms = [('phrase', p) for p in phrases] + [('word', w) for w in words]

        if terms:
            for term_type, text in terms:
                sql += """ AND (
                    LOWER(m.file_path) LIKE LOWER(?) OR
                    LOWER(m.title) LIKE LOWER(?) OR
                    LOWER(m.ref_content) LIKE LOWER(?) OR
                    LOWER(m.template) LIKE LOWER(?) OR
                    LOWER(m.caption) LIKE LOWER(?) OR
                    LOWER(m.description) LIKE LOWER(?) OR
                    LOWER(m.meaning) LIKE LOWER(?)
                )"""
                pattern = f"%{text}%"
                params.extend([pattern] * 7)
    
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
    
    # Add search filter to count query (must mirror the main query)
    if search_query:
        phrases = re.findall(r'"([^"]+)"', search_query)
        remaining = re.sub(r'"[^"]+"', " ", search_query)
        words = [w for w in remaining.split() if w]
        terms = [('phrase', p) for p in phrases] + [('word', w) for w in words]

        if terms:
            for term_type, text in terms:
                count_sql += """ AND (
                    LOWER(m.file_path) LIKE LOWER(?) OR
                    LOWER(m.title) LIKE LOWER(?) OR
                    LOWER(m.ref_content) LIKE LOWER(?) OR
                    LOWER(m.template) LIKE LOWER(?) OR
                    LOWER(m.caption) LIKE LOWER(?) OR
                    LOWER(m.description) LIKE LOWER(?) OR
                    LOWER(m.meaning) LIKE LOWER(?)
                )"""
                pattern = f"%{text}%"
                count_params.extend([pattern] * 7)
    
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
        memes_dir = get_memes_dir()
        
        # Calculate relative path for proper URLs
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            relative_path_str = relative_path.as_posix()
        except ValueError:
            relative_path_str = file_name
        
        # For videos, use preview GIF from thumbnails directory in _system
        if media_type == 'video':
            video_stem = Path(file_name).stem
            try:
                # Build thumbnail path in _system/thumbnails
                parent_relative = file_path_obj.parent.relative_to(Path(memes_dir))
                thumbnail_relative = Path('_system') / 'thumbnails' / parent_relative / f"{video_stem}_preview.gif"
                image_url = get_memes_url_base_dynamic() + thumbnail_relative.as_posix()
            except ValueError:
                # Fallback if path isn't relative to memes_dir
                image_url = get_memes_url_base_dynamic() + f"_system/thumbnails/{video_stem}_preview.gif"
            video_url = get_memes_url_base_dynamic() + relative_path_str
        elif media_type == 'gif':
            # Use the actual GIF (it will animate)
            image_url = get_memes_url_base_dynamic() + relative_path_str
            video_url = get_memes_url_base_dynamic() + relative_path_str
        else:
            image_url = get_memes_url_base_dynamic() + relative_path_str
            video_url = None
        
        album_previews = []
        if media_type == 'album':
            # Fetch up to 3 preview items for the album
            cursor.execute(
                """
                SELECT file_path FROM album_items
                WHERE album_id = ?
                ORDER BY display_order
                LIMIT 3
                """,
                (meme_id,)
            )
            item_paths = [r['file_path'] for r in cursor.fetchall()]
            for p in item_paths:
                p_obj = Path(p)
                try:
                    rel = p_obj.relative_to(Path(memes_dir))
                    album_previews.append(get_memes_url_base_dynamic() + rel.as_posix())
                except ValueError:
                    album_previews.append(get_memes_url_base_dynamic() + p_obj.name)

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
            'title': row['title'],
            'status': row['status'],
            'media_type': media_type,
            'description': row['description'],
            'error_message': row['error_message'],
            'tags': tags,
            'album_previews': album_previews
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
    
    # Get base URL for API calls (for multi-tenant support)
    base_url = request.environ.get('SCRIPT_NAME', '')
    
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
        show_pagination=total_pages > 1,
        clippy_agent=get_clippy_agent(),
        is_public_mode=is_public_mode(),
        base_url=base_url
    )

@app.route('/meme/<int:meme_id>', methods=['GET', 'POST'])
@login_required_unless_public
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
        # Require authentication for editing
        if not current_user.is_authenticated:
            conn.close()
            return redirect(url_for('login'))
        
        # Update meme details
        status = request.form.get('status', 'new')
        title = request.form.get('title', '').strip()
        ref_content = request.form.get('ref_content', '').strip()
        template = request.form.get('template', '').strip()
        caption = request.form.get('caption', '').strip()
        description = request.form.get('description', '').strip()
        meaning = request.form.get('meaning', '').strip()
        
        # Validate text field lengths (user-submitted only, AI can exceed)
        errors = []
        if len(title) > MAX_ALBUM_TITLE_LENGTH:
            errors.append(f'Album title too long (max {MAX_ALBUM_TITLE_LENGTH} characters)')
        if len(caption) > MAX_MEME_CAPTION_LENGTH:
            errors.append(f'Caption too long (max {MAX_MEME_CAPTION_LENGTH} characters)')
        if len(description) > MAX_MEME_DESCRIPTION_LENGTH:
            errors.append(f'Description too long (max {MAX_MEME_DESCRIPTION_LENGTH} characters)')
        if len(meaning) > MAX_MEME_MEANING_LENGTH:
            errors.append(f'Meaning too long (max {MAX_MEME_MEANING_LENGTH} characters)')
        if len(template) > MAX_MEME_TEMPLATE_LENGTH:
            errors.append(f'Template too long (max {MAX_MEME_TEMPLATE_LENGTH} characters)')
        if len(ref_content) > MAX_MEME_REF_CONTENT_LENGTH:
            errors.append(f'References too long (max {MAX_MEME_REF_CONTENT_LENGTH} characters)')
        
        if errors:
            conn.close()
            flash('; '.join(errors), 'error')
            return redirect(url_for('meme_detail', meme_id=meme_id))
        
        cursor.execute("""
            UPDATE memes 
            SET status = ?,
                title = ?,
                ref_content = ?,
                template = ?,
                caption = ?,
                description = ?,
                meaning = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, title or None, ref_content or None, template or None, caption or None, 
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
        SELECT id, file_path, title, media_type, status, ref_content, template, 
               caption, description, meaning, error_message, created_at, updated_at
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
    memes_dir = get_memes_dir()
    
    # Build proper URLs based on media type
    if media_type == 'video':
        # Use thumbnail for preview, original file for video player
        video_stem = file_path_obj.stem
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            video_url = get_memes_url_base_dynamic() + relative_path.as_posix()
            # Build thumbnail path in _system/thumbnails
            parent_relative = relative_path.parent
            thumbnail_relative = Path('_system') / 'thumbnails' / parent_relative / f"{video_stem}_thumb.jpg"
            image_url = get_memes_url_base_dynamic() + thumbnail_relative.as_posix()
        except ValueError:
            video_url = get_memes_url_base_dynamic() + file_name
            image_url = get_memes_url_base_dynamic() + f"_system/thumbnails/{video_stem}_thumb.jpg"
    elif media_type == 'album':
        # For albums, no single image; compute first item as default image
        cursor.execute(
            """
            SELECT file_path FROM album_items
            WHERE album_id = ?
            ORDER BY display_order
            """,
            (meme_id,)
        )
        album_item_paths = [r['file_path'] for r in cursor.fetchall()]
        album_item_urls = []
        for p in album_item_paths:
            p_obj = Path(p)
            try:
                rel = p_obj.relative_to(Path(memes_dir))
                album_item_urls.append(get_memes_url_base_dynamic() + rel.as_posix())
            except ValueError:
                album_item_urls.append(get_memes_url_base_dynamic() + p_obj.name)
        image_url = album_item_urls[0] if album_item_urls else None
        video_url = None
    else:
        # For images/gifs, calculate relative path for URL
        try:
            relative_path = file_path_obj.relative_to(Path(memes_dir))
            image_url = get_memes_url_base_dynamic() + relative_path.as_posix()
        except ValueError:
            image_url = get_memes_url_base_dynamic() + file_name
        video_url = None
    
    meme = {
        'id': row['id'],
        'image_url': image_url,
        'video_url': video_url,
        'file_name': file_name,
        'file_path': row['file_path'],
        'title': row['title'],
        'status': row['status'],
        'media_type': media_type,
        'ref_content': row['ref_content'] or '',
        'template': row['template'] or '',
        'caption': row['caption'] or '',
        'description': row['description'] or '',
        'meaning': row['meaning'] or '',
        'error_message': row['error_message'] or '',
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }

    # Attach album items for gallery if album (provide both url and path in order)
    album_items = []
    if media_type == 'album':
        album_items = [
            {'url': url, 'path': path}
            for url, path in zip(album_item_urls, album_item_paths)
        ]

    # Compute display path per rules: albums -> folder (full), others -> parent folder
    if media_type == 'album':
        display_path = str(file_path_obj)
    else:
        display_path = str(file_path_obj.parent)
    meme['display_path'] = display_path
    
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
            LOWER(m.file_path) LIKE LOWER(?) OR
            LOWER(m.ref_content) LIKE LOWER(?) OR
            LOWER(m.template) LIKE LOWER(?) OR
            LOWER(m.caption) LIKE LOWER(?) OR
            LOWER(m.description) LIKE LOWER(?) OR
            LOWER(m.meaning) LIKE LOWER(?)
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
            LOWER(m.file_path) LIKE LOWER(?) OR
            LOWER(m.ref_content) LIKE LOWER(?) OR
            LOWER(m.template) LIKE LOWER(?) OR
            LOWER(m.caption) LIKE LOWER(?) OR
            LOWER(m.description) LIKE LOWER(?) OR
            LOWER(m.meaning) LIKE LOWER(?)
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
    
    # Get base URL for API calls (for multi-tenant support)
    base_url = request.environ.get('SCRIPT_NAME', '')
    
    return render_template('meme_detail.html', meme=meme, album_items=album_items, saved=saved, all_tags=all_tags, current_tags=current_tags,
                          prev_id=prev_id, next_id=next_id, query_string=query_string, clippy_agent=get_clippy_agent(), is_public_mode=is_public_mode(),
                          base_url=base_url)

@app.route('/api/memes/<int:meme_id>', methods=['DELETE'])
@login_required
def delete_meme(meme_id):
    """Delete a meme from database and filesystem"""
    
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
@login_required
def bulk_delete():
    """Delete multiple memes"""
    
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
@login_required
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

@app.route('/api/albums/<int:album_id>/order', methods=['POST'])
@login_required
def update_album_order(album_id: int):
    """Update the display order of items in an album."""
    try:
        data = request.get_json(silent=True) or {}
        items = data.get('items', [])  # Expect list of file_path strings in desired order

        if not isinstance(items, list) or not items:
            return jsonify({'success': False, 'error': 'Invalid or empty items list'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Verify album exists and is of type 'album'
        cursor.execute("SELECT id FROM memes WHERE id = ? AND media_type = 'album'", (album_id,))
        if cursor.fetchone() is None:
            conn.close()
            return jsonify({'success': False, 'error': 'Album not found'}), 404

        # Fetch current item paths for validation
        cursor.execute(
            """
            SELECT file_path FROM album_items
            WHERE album_id = ?
            ORDER BY display_order
            """,
            (album_id,)
        )
        current_rows = cursor.fetchall()
        current_paths = [r['file_path'] if isinstance(r, sqlite3.Row) else r[0] for r in current_rows]

        # Optional strict validation: ensure same set of items
        if set(current_paths) != set(items):
            conn.close()
            return jsonify({'success': False, 'error': 'Items mismatch with current album'}), 400

        # Temporarily shift existing orders to avoid UNIQUE collisions during in-place updates
        cursor.execute(
            "UPDATE album_items SET display_order = display_order + 100000 WHERE album_id = ?",
            (album_id,)
        )

        # Update order to the new compact sequence
        for idx, path in enumerate(items, start=1):
            cursor.execute(
                """
                UPDATE album_items
                SET display_order = ?
                WHERE album_id = ? AND file_path = ?
                """,
                (idx, album_id, path)
            )

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bulk-memes-tags', methods=['POST'])
@login_required
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
@login_required
def settings():
    """Settings page with logs"""
    
    # Read log file - get last complete entry
    log_path = os.path.join(get_log_dir(), "scan.log")
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
    
    # Get current agent selection
    current_agent = get_clippy_agent()
    
    # Get base URL for API calls (for multi-tenant support)
    base_url = request.environ.get('SCRIPT_NAME', '')
    
    return render_template('settings.html', 
                         log_content=log_content, 
                         current_agent=current_agent, 
                         username=current_user.username,
                         base_url=base_url)

@app.route('/api/trigger-scheduled-scan', methods=['POST'])
@login_required
def trigger_scheduled_scan():
    """API endpoint to manually trigger the scheduled scan function (for testing)"""
    global _hourly_scan_function
    
    if 'INSTANCE_NAME' in app.config:
        return {'success': False, 'message': 'Not available in multi-tenant mode'}, 400
    
    if _hourly_scan_function is None:
        return {'success': False, 'message': 'Scheduled scan function not available'}, 500
    
    try:
        _hourly_scan_function()
        return {'success': True, 'message': 'Scheduled scan triggered successfully'}
    except Exception as e:
        app.logger.error(f"Failed to trigger scheduled scan: {e}")
        return {'success': False, 'message': f'Failed to trigger scan: {str(e)}'}, 500

@app.route('/api/trigger-action', methods=['POST'])
@login_required
def trigger_action():
    """API endpoint to trigger background actions"""
    data = request.get_json()
    action = data.get('action')
    
    if action == 'scan':
        # Run scan and process in background
        script_dir = get_script_dir()
        log_dir = get_log_dir()
        
        # Check if Replicate API key is configured
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'replicate_api_key'")
        api_key_row = cursor.fetchone()
        conn.close()
        
        if not (api_key_row and api_key_row[0].strip()):
            # Check if we have it in env (e.g. from sitecustomize or manual env var)
            if not os.environ.get('REPLICATE_API_TOKEN'):
                return {'success': False, 'message': 'Replicate API key not configured. Please set it in Settings first.'}
        
        try:
            # Set up environment variables for the shell script
            env = os.environ.copy()
            env['SCRIPT_DIR'] = script_dir  # Instance directory (for files, logs, etc.)
            env['LOG_DIR'] = log_dir
            env['DB_PATH'] = get_db_path()
            env['MEMES_DIR'] = get_memes_dir()
            env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
            env['VENV_DIR'] = get_venv_dir()
            # Ensure PATH includes standard locations for bash
            env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
            
            # Script resolution using configuration
            script_dir = app.config.get('HELPER_SCRIPTS_DIR', get_script_dir())
            shell_script = os.path.join(script_dir, 'run_scan.sh')
            
            # Use full path to bash to avoid PATH issues in systemd
            bash_path = '/bin/bash'
            if not os.path.exists(bash_path):
                bash_path = '/usr/bin/bash'
            
            subprocess.Popen(
                [bash_path, shell_script],
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(log_dir, 'scan_errors.log'), 'a'),
                env=env,
                start_new_session=True
            )
            return {'success': True, 'message': 'Scan started in background! Check logs for progress.'}
        except Exception as e:
            return {'success': False, 'message': f'Failed to start scan: {str(e)}'}
    
    elif action == 'retry_errors':
        # Run retry errors in background using dedicated script
        script_dir = get_script_dir()
        log_dir = get_log_dir()
        
        try:
            # Set up environment variables for the shell script
            env = os.environ.copy()
            env['SCRIPT_DIR'] = script_dir  # Instance directory (for files, logs, etc.)
            env['LOG_DIR'] = log_dir
            env['DB_PATH'] = get_db_path()
            env['MEMES_DIR'] = get_memes_dir()
            env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
            env['VENV_DIR'] = get_venv_dir()
            
            # Script resolution using configuration
            script_dir = app.config.get('HELPER_SCRIPTS_DIR', get_script_dir())
            shell_script = os.path.join(script_dir, 'retry_errors.sh')

            
            subprocess.Popen(
                [shell_script],
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(log_dir, 'scan_errors.log'), 'a'),
                env=env,
                start_new_session=True
            )
            return {'success': True, 'message': 'Error reprocessing started in background! Check logs for progress.'}
        except Exception as e:
            return {'success': False, 'message': f'Failed to start retry: {str(e)}'}
    
    elif action == 'scan_tags_all':
        # Run tags-only scan for all memes using process_memes.py
        from datetime import datetime
        instance_dir = get_script_dir()  # Instance directory
        venv_dir = get_venv_dir()
        venv_python = os.path.join(venv_dir, "bin", "python")
        
        # Script resolution using configuration
        script_dir = app.config.get('HELPER_SCRIPTS_DIR', instance_dir)
        script_path = os.path.join(script_dir, 'process_memes.py')
        working_dir = script_dir  # Run in the script directory to ensure imports work

        
        log_file = os.path.join(get_log_dir(), "scan.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        try:
            # Set up environment variables for the Python script
            env = os.environ.copy()
            env['SCRIPT_DIR'] = instance_dir  # Instance directory (for files, logs, etc.)
            env['LOG_DIR'] = get_log_dir()
            env['DB_PATH'] = get_db_path()
            env['MEMES_DIR'] = get_memes_dir()
            env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
            env['VENV_DIR'] = get_venv_dir()
            
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, 'a', encoding='utf-8') as lf:
                lf.write("================================\n")
                lf.write(f"{ts}: Triggered tags-only scan for ALL memes via UI\n")
                lf.write(f"Process script: {script_path}\n")
                lf.write(f"Working dir: {working_dir}\n")
                lf.write("================================\n")
                python_exec = venv_python if os.path.exists(venv_python) else "python3"
                subprocess.Popen(
                    [python_exec, script_path, '--scan-tags-all'],
                    cwd=working_dir,
                    env=env,
                    stdout=lf,
                    stderr=lf,
                    start_new_session=True
                )
            return {'success': True, 'message': 'Tags-only scan started in background!'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    return {'success': False, 'message': 'Invalid action'}

@app.route('/api/memes/<int:meme_id>/scan-tags', methods=['POST'])
@login_required
def scan_tags_single_meme(meme_id: int):
    """Trigger a tags-only scan for a single meme (path + AI-from-text)."""
    import subprocess
    from datetime import datetime

    script_dir = get_script_dir()
    venv_dir = get_venv_dir()
    venv_python = os.path.join(venv_dir, "bin", "python")
    script_path = os.path.join(script_dir, "process_memes.py")
    log_file = os.path.join(get_log_dir(), "scan.log")

    # Ensure logs directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Log header
    try:
        job_id = str(uuid4())
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, 'a', encoding='utf-8') as lf:
            lf.write("================================\n")
            lf.write(f"{ts}: TAGSCAN JOB {job_id} START id={meme_id}\n")
            lf.write("================================\n")
            python_exec = venv_python if os.path.exists(venv_python) else "python3"
            subprocess.Popen(
                [python_exec, script_path, '--scan-tags-one', str(meme_id), '--job-id', job_id],
                cwd=script_dir,
                stdout=lf,
                stderr=lf,
                start_new_session=True
            )
        return {'success': True, 'message': 'Tag scan started', 'job_id': job_id}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500

@app.route('/api/bulk-scan-tags', methods=['POST'])
@login_required
def bulk_scan_tags():
    """Trigger tags-only scan for a set of selected meme IDs."""
    import subprocess
    from datetime import datetime
    data = request.get_json(silent=True) or {}
    meme_ids = data.get('meme_ids', [])
    if not meme_ids:
        return {'success': False, 'error': 'No meme IDs provided'}
    # Build comma-separated string of IDs
    try:
        id_list = [str(int(i)) for i in meme_ids]
    except Exception:
        return {'success': False, 'error': 'Invalid meme IDs'}, 400

    ids_str = ",".join(id_list)

    script_dir = get_script_dir()
    venv_dir = get_venv_dir()
    venv_python = os.path.join(venv_dir, "bin", "python")
    script_path = os.path.join(script_dir, "process_memes.py")
    log_file = os.path.join(get_log_dir(), "scan.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    try:
        with open(log_file, 'a', encoding='utf-8') as lf:
            lf.write("================================\n")
            lf.write(f"{datetime.now()}: Triggered tags-only scan via UI (ids={ids_str})\n")
            lf.write("================================\n")
            python_exec = venv_python if os.path.exists(venv_python) else "python3"
            subprocess.Popen(
                [python_exec, script_path, '--scan-tags-ids', ids_str],
                cwd=script_dir,
                stdout=lf,
                stderr=lf,
                start_new_session=True
            )
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500

@app.route('/api/jobs/<job_id>/status', methods=['GET'])
@login_required
def job_status(job_id: str):
    """Return scan-tag job status by scanning the log for the COMPLETE marker."""
    import re
    log_file = os.path.join(get_log_dir(), "scan.log")
    if not os.path.exists(log_file):
        return {'success': True, 'status': 'pending'}

    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Example complete line: TAGSCAN JOB <job_id> COMPLETE id=123 applied=true
        pattern = rf"TAGSCAN JOB {re.escape(job_id)} COMPLETE .*?applied=(true|false)"
        m = re.search(pattern, content)
        if m:
            applied = (m.group(1).lower() == 'true')
            return {'success': True, 'status': 'completed', 'applied': applied}
        if f"TAGSCAN JOB {job_id} START" in content:
            return {'success': True, 'status': 'pending'}
        return {'success': True, 'status': 'pending'}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500

@app.route('/api/memes/<int:meme_id>/process', methods=['POST'])
@login_required
def process_single_meme(meme_id: int):
    """Trigger processing of a single meme in background and log to scan.log."""
    import subprocess
    from datetime import datetime

    instance_dir = get_script_dir()  # Instance directory
    venv_dir = get_venv_dir()
    venv_python = os.path.join(venv_dir, "bin", "python")
    
    # Script resolution using configuration
    script_dir = app.config.get('HELPER_SCRIPTS_DIR', instance_dir)
    script_path = os.path.join(script_dir, 'process_memes.py')
    working_dir = script_dir

    
    log_file = os.path.join(get_log_dir(), "scan.log")

    # Mark meme as processing in DB
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE memes
            SET status='processing', error_message=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (meme_id,)
        )
        conn.commit()
        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

    # Ensure logs directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Prepend a header line to the log synchronously
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, 'a', encoding='utf-8') as lf:
            lf.write("================================\n")
            lf.write(f"{ts}: Triggered single meme processing via UI (id={meme_id})\n")
            lf.write(f"Process script: {script_path}\n")
            lf.write(f"Working dir: {working_dir}\n")
            lf.write("================================\n")
    except Exception:
        # Non-fatal
        pass

    # Launch processing in background, append stdout/stderr to log
    try:
        # Set up environment variables for the processing script
        env = os.environ.copy()
        env['SCRIPT_DIR'] = instance_dir  # Instance directory (for files, logs, etc.)
        env['LOG_DIR'] = get_log_dir()
        env['DB_PATH'] = get_db_path()
        env['MEMES_DIR'] = get_memes_dir()
        env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
        env['VENV_DIR'] = venv_dir
        
        # Prefer venv python if exists, else rely on system python
        python_exec = venv_python if os.path.exists(venv_python) else "python3"
        with open(log_file, 'a', encoding='utf-8') as lf:
            proc = subprocess.Popen(
                [python_exec, script_path, '--process-one', str(meme_id)],
                cwd=working_dir,
                env=env,
                stdout=lf,
                stderr=lf,
                start_new_session=True
            )
            
            # Start a background thread to monitor the process and update status on failure
            def monitor_processing():
                import threading
                import time
                try:
                    # Wait for process to complete (max 60 seconds)
                    exit_code = proc.wait(timeout=60)
                    if exit_code != 0:
                        # Processing failed, update meme status to error
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (f"Manual processing failed with exit code {exit_code}", meme_id)
                        )
                        conn.commit()
                        conn.close()
                        lf.write(f"Updated meme {meme_id} status to error (exit code: {exit_code})\n")
                except subprocess.TimeoutExpired:
                    # Process is still running after 60 seconds, that's normal for processing
                    pass
                except Exception as monitor_error:
                    lf.write(f"Error monitoring process for meme {meme_id}: {monitor_error}\n")
            
            # Start monitoring in a separate thread
            import threading
            monitor_thread = threading.Thread(target=monitor_processing, daemon=True)
            monitor_thread.start()
            
        return {'success': True, 'message': 'Processing started'}
    except Exception as e:
        # If we can't even start processing, mark as error immediately
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (f"Failed to start manual processing: {str(e)}", meme_id)
            )
            conn.commit()
            conn.close()
        except Exception as db_error:
            pass  # If DB update also fails, we can't do much
        return {'success': False, 'error': str(e)}, 500

@app.route('/api/memes/<int:meme_id>', methods=['GET'])
@login_required
def get_meme(meme_id: int):
    """Return current meme fields for polling/progress UI."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, title, status, media_type, file_path, ref_content, template, caption, description, meaning, error_message, created_at, updated_at
        FROM memes WHERE id = ?
        """,
        (meme_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {'success': False, 'error': 'Meme not found'}, 404
    # Fetch current tag ids for this meme
    cursor.execute("SELECT tag_id FROM meme_tags WHERE meme_id = ?", (meme_id,))
    tag_rows = cursor.fetchall()
    tag_ids = [r[0] if not isinstance(r, sqlite3.Row) else r['tag_id'] for r in tag_rows]
    conn.close()
    resp = jsonify({
        'success': True,
        'meme': {
            'id': row['id'],
            'title': row['title'],
            'status': row['status'],
            'media_type': row['media_type'],
            'file_path': row['file_path'],
            'ref_content': row['ref_content'],
            'template': row['template'],
            'caption': row['caption'],
            'description': row['description'],
            'meaning': row['meaning'],
            'error_message': row['error_message'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            'tag_ids': tag_ids,
        }
    })
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/random-meme', methods=['GET'])
@login_required_unless_public
def get_random_meme():
    """Get a random meme ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM memes ORDER BY RANDOM() LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'success': False, 'error': 'No memes found'}), 404
    
    meme_id = row['id'] if isinstance(row, sqlite3.Row) else row[0]
    return jsonify({'success': True, 'meme_id': meme_id})

@app.route('/tags')
@login_required
def tags():
    """Tag management page - always requires authentication"""
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
    
    # Get base URL for API calls (for multi-tenant support)
    base_url = request.environ.get('SCRIPT_NAME', '')
    
    return render_template('tags.html', tags=tags_list, clippy_agent=get_clippy_agent(), base_url=base_url)

@app.route('/api/tags', methods=['POST'])
@login_required
def create_tag():
    """Create a new tag"""
    data = request.get_json()
    
    # Validate tag name and description lengths
    tag_name = data.get('name', '').strip()
    tag_description = data.get('description', '').strip()
    
    if not tag_name:
        return {'success': False, 'error': 'Tag name is required'}
    
    if len(tag_name) > MAX_TAG_NAME_LENGTH:
        return {'success': False, 'error': f'Tag name too long (max {MAX_TAG_NAME_LENGTH} characters)'}
    
    if len(tag_description) > MAX_TAG_DESCRIPTION_LENGTH:
        return {'success': False, 'error': f'Tag description too long (max {MAX_TAG_DESCRIPTION_LENGTH} characters)'}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO tags (name, description, color, parse_from_filename, ai_can_suggest)
            VALUES (?, ?, ?, ?, ?)
        """, (
            tag_name,
            tag_description,
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
@login_required
def update_tag(tag_id):
    """Update a tag"""
    data = request.get_json()
    
    # Validate tag name and description lengths
    tag_name = data.get('name', '').strip()
    tag_description = data.get('description', '').strip()
    
    if not tag_name:
        return {'success': False, 'error': 'Tag name is required'}
    
    if len(tag_name) > MAX_TAG_NAME_LENGTH:
        return {'success': False, 'error': f'Tag name too long (max {MAX_TAG_NAME_LENGTH} characters)'}
    
    if len(tag_description) > MAX_TAG_DESCRIPTION_LENGTH:
        return {'success': False, 'error': f'Tag description too long (max {MAX_TAG_DESCRIPTION_LENGTH} characters)'}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE tags
        SET name = ?, description = ?, color = ?, 
            parse_from_filename = ?, ai_can_suggest = ?
        WHERE id = ?
    """, (
        tag_name,
        tag_description,
        data['color'],
        1 if data.get('parse_from_filename', True) else 0,
        1 if data.get('ai_can_suggest', True) else 0,
        tag_id
    ))
    
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def delete_tag(tag_id):
    """Delete a tag"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    
    conn.commit()
    conn.close()
    
    return {'success': True}

@app.route('/api/clippy-agents', methods=['GET'])
@login_required
def get_clippy_agents():
    """Get list of available Clippy agents"""
    agents_dir = Path(app.static_folder) / 'clippy' / 'agents'
    agents = []
    
    if not agents_dir.exists():
        return jsonify({'success': False, 'error': 'Agents directory not found'})
    
    # Scan agent directories
    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        
        agent_name = agent_dir.name
        agent_js = agent_dir / 'agent.js'
        
        # Include agents that have an agent.js file (which means they're valid agents)
        # The frontend will handle missing preview.png files gracefully
        if agent_js.exists():
            agents.append({
                'name': agent_name
            })
    
    # Sort agents by name
    agents.sort(key=lambda x: x['name'])
    
    return jsonify({'success': True, 'agents': agents})

@app.route('/api/settings/clippy-agent', methods=['GET'])
@login_required
def get_clippy_agent_setting():
    """Get current Clippy agent selection"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    cursor.execute("SELECT value FROM settings WHERE key = 'agent_form'")
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('agent_form', 'none')")
        conn.commit()
        agent_form = 'none'
    else:
        agent_form = row[0]
    
    conn.close()
    
    return jsonify({'success': True, 'agent_form': agent_form})

@app.route('/api/settings/clippy-agent', methods=['POST'])
@login_required
def set_clippy_agent_setting():
    """Save Clippy agent selection"""
    data = request.get_json()
    agent_form = data.get('agent_form', 'none')
    
    # Validate agent_form (should be 'none' or a valid agent name)
    if agent_form != 'none':
        # Check if agent exists
        agents_dir = Path(app.static_folder) / 'clippy' / 'agents' / agent_form
        if not agents_dir.exists():
            return jsonify({'success': False, 'error': 'Invalid agent name'})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('agent_form', ?)",
        (agent_form,)
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'agent_form': agent_form})

@app.route('/api/settings/disk-usage', methods=['GET'])
@login_required
def get_disk_usage():
    """Get disk usage information"""
    try:
        instance_path = Path(get_instance_path())
        disk_quota_mb = get_disk_quota_mb()
        
        # Calculate current disk usage
        total_size = 0
        for item in instance_path.rglob('*'):
            if item.is_file():
                try:
                    total_size += item.stat().st_size
                except (OSError, PermissionError):
                    pass
        
        current_usage_mb = total_size / (1024 * 1024)
        
        if disk_quota_mb is not None:
            # Quota configured
            remaining_mb = max(0, disk_quota_mb - current_usage_mb)
            percent_used = min(100, (current_usage_mb / disk_quota_mb) * 100) if disk_quota_mb > 0 else 0
            
            return jsonify({
                'success': True,
                'quota_configured': True,
                'current_usage_mb': round(current_usage_mb, 2),
                'disk_quota_mb': disk_quota_mb,
                'remaining_mb': round(remaining_mb, 2),
                'percent_used': round(percent_used, 1)
            })
        else:
            # No quota - show filesystem free space
            import shutil
            stat = shutil.disk_usage(instance_path)
            total_mb = stat.total / (1024 * 1024)
            used_mb = stat.used / (1024 * 1024)
            free_mb = stat.free / (1024 * 1024)
            percent_used = (stat.used / stat.total) * 100 if stat.total > 0 else 0
            
            return jsonify({
                'success': True,
                'quota_configured': False,
                'current_usage_mb': round(current_usage_mb, 2),
                'filesystem_total_mb': round(total_mb, 2),
                'filesystem_used_mb': round(used_mb, 2),
                'filesystem_free_mb': round(free_mb, 2),
                'percent_used': round(percent_used, 1)
            })
            
    except Exception as e:
        app.logger.error(f"Error getting disk usage: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/update', methods=['POST'])
@login_required
def initiate_update():
    """
    Initiate version update.
    For single-tenant: Logs out user and redirects to login with ?v= parameter.
    For multi-tenant: Returns error (handled by memelord).
    """
    # Only allow in single-tenant mode
    if 'INSTANCE_NAME' in app.config:
        return jsonify({
            'success': False,
            'error': 'Updates are handled by memelord in multi-tenant mode'
        }), 403
    
    try:
        # Get available version and current branch
        available_version = get_available_version()
        current_branch = get_current_branch()
        update_info = check_for_updates()
        
        if not update_info['needs_update']:
            return jsonify({
                'success': False,
                'error': 'No update available or already on latest version'
            }), 400
        
        if not available_version and current_branch != 'dev':
            return jsonify({
                'success': False,
                'error': 'Could not determine available version'
            }), 400
        
        # Log out the user server-side
        logout_user()
        
        # Determine redirect URL based on branch
        if current_branch == 'dev':
            # For dev branch, use ?update=dev (no version needed)
            redirect_url = url_for('login', update='dev', _external=False)
        else:
            # For other branches, use ?v=version
            redirect_url = url_for('login', v=available_version, _external=False)
        
        # Return redirect response (client will follow it)
        return jsonify({
            'success': True,
            'redirect_url': redirect_url,
            'message': f'Redirecting to update to version {available_version or "latest"}...'
        })
    
    except Exception as e:
        app.logger.error(f"Error initiating update: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/settings/version', methods=['GET'])
@login_required
def get_version_info():
    """Get version information: current version, branch, available version, and update status"""
    try:
        # Sync branch from config.json if in multi-tenant mode (config.json is source of truth)
        if 'INSTANCE_NAME' in app.config:
            try:
                instance_path = Path(get_instance_path())
                config_file = instance_path / 'config.json'
                if config_file.exists():
                    import json
                    with open(config_file, 'r') as f:
                        instance_config = json.load(f)
                        git_branch = instance_config.get('git_branch')
                        if git_branch:
                            # Sync branch from config.json to database
                            db_branch = get_current_branch()
                            if db_branch != git_branch:
                                set_current_branch(git_branch)
                                app.logger.info(f"Synced branch from config.json: {git_branch}")
            except Exception as e:
                app.logger.warning(f"Could not sync branch from config.json: {e}")
        
        current_branch = get_current_branch()
        
        # For dev branch, show commit hash instead of version
        if current_branch == 'dev':
            commit_info = get_dev_commit_info()
            if commit_info and commit_info.get('current_commit'):
                # Use commit hash as current version for dev branch
                current_version = f"commit:{commit_info['current_commit']}"
            else:
                # Not a git repo or git commands failed - show helpful message
                # Check if .git exists to give better error message
                install_dir = Path(get_install_dir())
                git_dir = install_dir / '.git'
                # Check if git command is available
                git_cmd = find_git_executable()
                git_available = git_cmd is not None
                
                if not git_dir.exists():
                    # Not a git repository - can't show commit info
                    current_version = "Not a git repository"
                elif not git_available:
                    # Git repo exists but git command not available
                    current_version = "Git command not available"
                else:
                    # Git repo exists but commit info failed - use CHANGELOG as fallback
                    current_version = get_current_version()
                    if not current_version:
                        from init_database import get_version_from_changelog
                        changelog_version = get_version_from_changelog()
                        if changelog_version:
                            set_current_version(changelog_version)
                            current_version = changelog_version
        else:
            # For other branches, sync version from CHANGELOG.md if not set
            current_version = get_current_version()
            if not current_version:
                # Try to get version from CHANGELOG.md and update database
                from init_database import get_version_from_changelog
                changelog_version = get_version_from_changelog()
                if changelog_version:
                    set_current_version(changelog_version)
                    current_version = changelog_version
                    app.logger.info(f"Synced version from CHANGELOG.md: {changelog_version}")
        
        available_version = get_available_version()
        update_info = check_for_updates()
        
        # Update last_update_check timestamp (we just checked for updates)
        set_last_update_check()
        
        # Get last update check timestamp (just set above, but fetch for consistency)
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'last_update_check'")
            row = cursor.fetchone()
            last_update_check = row[0] if row and row[0] else None
            conn.close()
        except Exception:
            last_update_check = None
        
        return jsonify({
            'success': True,
            'current_version': current_version,
            'current_branch': current_branch,
            'available_version': available_version,
            'update_available': update_info['update_available'],
            'needs_update': update_info['needs_update'],
            'last_update_check': last_update_check
        })
    except Exception as e:
        app.logger.error(f"Error getting version info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/replicate-usage', methods=['GET'])
@login_required
def get_replicate_usage():
    """Get Replicate usage/quota information for this instance."""
    try:
        # Check if user has their own key (BYOK) - if so, quotas don't apply
        has_db_key = False
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'replicate_api_key'")
            row = cursor.fetchone()
            if row and row[0] and row[0].strip():
                has_db_key = True
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # If user has their own key, quotas don't apply (they bypass the proxy)
        if has_db_key:
            return jsonify({
                'success': True,
                'quota_configured': False
            })

        quota_limit = get_replicate_quota_limit()

        if quota_limit is None:
            # No managed quota – caller can choose to hide any quota UI
            return jsonify({
                'success': True,
                'quota_configured': False
            })

        # Fetch current usage dynamically if managed (env var is only set at startup)
        # If REPLICATE_MANAGED_PROXY is set, fetch from managing service
        used = None
        if os.environ.get('REPLICATE_MANAGED_PROXY') == '1':
            # Try to fetch current usage from managing service
            # Read config.json to get proxy URL (same pattern as sitecustomize.py uses)
            try:
                import requests
                import json
                from pathlib import Path
                
                # Get instance name from app config (set by wrapper in multi-tenant setup)
                instance_name = app.config.get('INSTANCE_NAME')
                if instance_name:
                    # Read config.json to get proxy URL (generic - just reads a config file)
                    instance_path = get_instance_path()
                    config_path = Path(instance_path) / 'config.json'
                    if config_path.exists():
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                        proxy_url = config.get('memelord_proxy_url')
                        if proxy_url:
                            # Derive usage URL from proxy URL (generic pattern)
                            usage_url = proxy_url.replace('/api/replicate-proxy', f'/api/replicate-usage/{instance_name}')
                            try:
                                response = requests.get(usage_url, timeout=5)
                                if response.status_code == 200:
                                    usage_data = response.json()
                                    used = usage_data.get('used', 0)
                            except Exception:
                                pass
            except Exception:
                pass
        
        # Fallback to env var if dynamic fetch didn't work
        if used is None:
            used = get_replicate_quota_used() or 0

        remaining = max(0, quota_limit - used)
        percent_used = min(100, (used / quota_limit) * 100) if quota_limit > 0 else 0

        return jsonify({
            'success': True,
            'quota_configured': True,
            'requests_used': used,
            'quota_limit': quota_limit,
            'remaining_requests': remaining,
            'percent_used': round(percent_used, 1),
        })
    except Exception as e:
        app.logger.error(f"Error getting Replicate usage: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/replicate-api-key', methods=['GET'])
@login_required
def get_replicate_api_key_setting():
    """Get Replicate API key (masked for security)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    cursor.execute("SELECT value FROM settings WHERE key = 'replicate_api_key'")
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO settings (key, value) VALUES ('replicate_api_key', '')")
        conn.commit()
        api_key = ''
    else:
        api_key = row[0] or ''
    
    conn.close()
    
    # Mask the API key for display (show only last 4 characters)
    if api_key and len(api_key) > 4:
        masked_key = '•' * (len(api_key) - 4) + api_key[-4:]
    else:
        masked_key = api_key
    
    return jsonify({'success': True, 'api_key': masked_key, 'has_key': bool(api_key)})

@app.route('/api/settings/replicate-api-key', methods=['POST'])
@login_required
def set_replicate_api_key_setting():
    """Save Replicate API key"""
    data = request.get_json()
    api_key = data.get('api_key', '').strip()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('replicate_api_key', ?)",
        (api_key,)
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'API key saved successfully'})

@app.route('/api/settings/ai-enabled', methods=['GET'])
@login_required
def get_ai_enabled_setting():
    """Get AI enabled status"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Get AI enabled setting (default is enabled/true)
    cursor.execute("SELECT value FROM settings WHERE key = 'ai_enabled'")
    result = cursor.fetchone()
    conn.close()
    
    # Default to True (enabled) if not set
    ai_enabled = True
    if result and result[0]:
        ai_enabled = result[0].lower() in ('true', '1', 'yes')
    
    return jsonify({'success': True, 'ai_enabled': ai_enabled})

@app.route('/api/settings/ai-enabled', methods=['POST'])
@login_required
def set_ai_enabled_setting():
    """Save AI enabled status"""
    data = request.get_json()
    ai_enabled = data.get('ai_enabled', True)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure settings table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Store as string ('true' or 'false')
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('ai_enabled', ?)",
        (str(ai_enabled).lower(),)
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'AI enabled status updated', 'ai_enabled': ai_enabled})

@app.route('/api/settings/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password"""
    data = request.get_json()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')
    
    if not current_password or not new_password or not confirm_password:
        return jsonify({'success': False, 'error': 'All fields are required'}), 400
    
    if new_password != confirm_password:
        return jsonify({'success': False, 'error': 'New passwords do not match'}), 400
    
    if len(new_password) < 4:
        return jsonify({'success': False, 'error': 'Password must be at least 4 characters'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify current password
    cursor.execute("SELECT password_hash FROM users WHERE id = ?", (current_user.id,))
    row = cursor.fetchone()
    
    if not row or not check_password_hash(row['password_hash'], current_password):
        conn.close()
        return jsonify({'success': False, 'error': 'Current password is incorrect'}), 400
    
    # Update password
    new_password_hash = generate_password_hash(new_password)
    cursor.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_password_hash, current_user.id)
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Password changed successfully'})

@app.route('/api/settings/privacy-mode', methods=['GET'])
@login_required
def get_privacy_mode():
    """Get current privacy mode setting"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'privacy_mode'")
    row = cursor.fetchone()
    privacy_mode = row['value'] if row else 'private'
    conn.close()
    return jsonify({'success': True, 'privacy_mode': privacy_mode})

@app.route('/api/settings/privacy-mode', methods=['POST'])
@login_required
def set_privacy_mode():
    """Set privacy mode (private/public)"""
    data = request.get_json()
    privacy_mode = data.get('privacy_mode', 'private')
    
    if privacy_mode not in ['private', 'public']:
        return jsonify({'success': False, 'error': 'Invalid privacy mode'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('privacy_mode', ?)",
        (privacy_mode,)
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'privacy_mode': privacy_mode})

def get_file_hash(file_path):
    """Compute SHA256 hash of file contents"""
    try:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

def get_unique_filename(directory, filename):
    """Get a unique filename by appending numbers if file exists"""
    file_path = Path(directory) / filename
    if not file_path.exists():
        return filename
    
    name_stem = file_path.stem
    extension = file_path.suffix
    counter = 1
    
    while True:
        new_filename = f"{name_stem}_{counter}{extension}"
        new_path = Path(directory) / new_filename
        if not new_path.exists():
            return new_filename
        counter += 1

def determine_media_type(filename):
    """Determine media type from filename extension"""
    ext = Path(filename).suffix.lower()
    if ext in GIF_EXTENSIONS:
        return 'gif'
    elif ext in VIDEO_EXTENSIONS:
        return 'video'
    elif ext in IMAGE_EXTENSIONS:
        return 'image'
    return None

@app.route('/api/upload', methods=['POST'])
@login_required
def upload_files():
    """Handle file uploads"""
    try:
        # Get upload mode
        mode = request.form.get('mode', 'single')
        
        if mode not in ['single', 'album']:
            return jsonify({'success': False, 'error': 'Invalid upload mode'}), 400
        
        # Get uploaded files
        files = request.files.getlist('files')
        
        if not files or len(files) == 0:
            return jsonify({'success': False, 'error': 'No files provided'}), 400
        
        # Ensure upload directories exist
        ensure_directories_exist()
        
        # Check disk space (either quota or filesystem free space)
        disk_quota_mb = get_disk_quota_mb()
        try:
            instance_path = Path(get_instance_path())
            
            if disk_quota_mb is not None:
                # Use configured quota
                total_size = 0
                for item in instance_path.rglob('*'):
                    if item.is_file():
                        try:
                            total_size += item.stat().st_size
                        except (OSError, PermissionError):
                            pass
                
                current_usage_mb = total_size / (1024 * 1024)
                remaining_mb = disk_quota_mb - current_usage_mb
                quota_type = "quota"
            else:
                # No quota configured - use filesystem free space
                import shutil
                stat = shutil.disk_usage(instance_path)
                remaining_mb = stat.free / (1024 * 1024)
                current_usage_mb = 0  # Not applicable for filesystem check
                quota_type = "filesystem"
            
            # Calculate upload size
            upload_size = request.content_length or 0
            upload_size_mb = upload_size / (1024 * 1024)
            
            # Check if upload would exceed available space
            if upload_size_mb > remaining_mb:
                app.logger.warning(f"Upload rejected: would exceed available space by {upload_size_mb - remaining_mb:.2f}MB")
                
                if disk_quota_mb is not None:
                    # Quota-based error
                    return jsonify({
                        'error': 'Insufficient disk space',
                        'message': f'Upload size ({upload_size_mb:.2f}MB) exceeds remaining quota ({remaining_mb:.2f}MB)',
                        'current_usage_mb': round(current_usage_mb, 2),
                        'disk_quota_mb': disk_quota_mb,
                        'remaining_mb': round(remaining_mb, 2),
                        'upload_size_mb': round(upload_size_mb, 2)
                    }), 413
                else:
                    # Filesystem-based error
                    return jsonify({
                        'error': 'Insufficient disk space',
                        'message': f'Upload size ({upload_size_mb:.2f}MB) exceeds available disk space ({remaining_mb:.2f}MB)',
                        'free_space_mb': round(remaining_mb, 2),
                        'upload_size_mb': round(upload_size_mb, 2)
                    }), 413
                
        except Exception as e:
            app.logger.error(f"Error checking disk space: {e}")
            # Don't block upload if space check fails
        
        # Validate file types and sizes
        for file in files:
            if not file.filename:
                continue
            
            ext = Path(file.filename).suffix.lower()
            
            if mode == 'album':
                # Only images for albums
                if ext not in IMAGE_EXTENSIONS:
                    return jsonify({
                        'success': False, 
                        'error': f'Invalid file type for album: {file.filename}. Only images allowed.'
                    }), 400
            else:
                # All media types for single mode
                if ext not in ALL_MEDIA_EXTENSIONS:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid file type: {file.filename}'
                    }), 400
            
            # Validate file size
            file.seek(0, 2)  # Seek to end of file
            file_size_bytes = file.tell()
            file.seek(0)  # Reset to beginning
            file_size_mb = file_size_bytes / (1024 * 1024)
            
            if ext in VIDEO_EXTENSIONS:
                max_size_mb = MAX_VIDEO_SIZE_MB
                file_type_name = "video"
            elif ext in GIF_EXTENSIONS:
                max_size_mb = MAX_GIF_SIZE_MB
                file_type_name = "GIF"
            else:  # IMAGE_EXTENSIONS
                max_size_mb = MAX_IMAGE_SIZE_MB
                file_type_name = "image"
            
            if file_size_mb > max_size_mb:
                return jsonify({
                    'success': False,
                    'error': f'{file.filename}: {file_type_name} file too large ({file_size_mb:.1f}MB). Maximum size is {max_size_mb}MB.'
                }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        meme_ids = []
        
        if mode == 'album':
            # Create album directory with timestamp
            timestamp = datetime.now().strftime("%d%m%y-%H%M")
            album_name = f"album_{timestamp}"
            album_dir = get_albums_dir() / album_name
            album_dir.mkdir(parents=True, exist_ok=True)
            
            # Save all files to album directory
            album_item_paths = []
            for file in files:
                if not file.filename:
                    continue
                
                filename = secure_filename(file.filename)
                unique_filename = get_unique_filename(album_dir, filename)
                file_path = album_dir / unique_filename
                
                file.save(str(file_path))
                album_item_paths.append(str(file_path.resolve()))
            
            if not album_item_paths:
                conn.close()
                return jsonify({'success': False, 'error': 'No valid files uploaded'}), 400
            
            # Create album entry in database
            cursor.execute(
                "INSERT INTO memes (file_path, title, media_type, status) VALUES (?, ?, 'album', 'new')",
                (str(album_dir.resolve()), album_name)
            )
            album_id = cursor.lastrowid
            
            # Add album items
            for order, item_path in enumerate(album_item_paths, start=1):
                file_hash = get_file_hash(item_path)
                cursor.execute(
                    "INSERT INTO album_items (album_id, file_path, display_order, file_hash) VALUES (?, ?, ?, ?)",
                    (album_id, item_path, order, file_hash)
                )
            
            conn.commit()
            meme_ids.append(album_id)

            # Trigger processing for the album (single-instance first, multi-tenant aware via HELPER_SCRIPTS_DIR)
            try:
                instance_dir = get_script_dir()  # Instance directory
                venv_dir = get_venv_dir()

                # Resolve process_memes.py location using helper scripts dir when provided
                script_dir = app.config.get('HELPER_SCRIPTS_DIR', instance_dir)
                process_script = os.path.join(script_dir, 'process_memes.py')

                log_file = os.path.join(get_log_dir(), 'scan.log')
                os.makedirs(os.path.dirname(log_file), exist_ok=True)

                # Environment for processing script
                env = os.environ.copy()
                env['SCRIPT_DIR'] = instance_dir  # Instance directory (for files, logs, etc.)
                env['LOG_DIR'] = get_log_dir()
                env['DB_PATH'] = get_db_path()
                env['MEMES_DIR'] = get_memes_dir()
                env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
                env['VENV_DIR'] = venv_dir

                working_dir = script_dir

                with open(log_file, 'a', encoding='utf-8') as lf:
                    lf.write("================================\n")
                    lf.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Processing uploaded album (id={album_id})\n")
                    lf.write(f"Process script: {process_script}\n")
                    lf.write(f"Working dir: {working_dir}\n")
                    lf.write("================================\n")

                    python_exec = os.path.join(venv_dir, "bin", "python") if os.path.exists(os.path.join(venv_dir, "bin", "python")) else "python3"

                    proc = subprocess.Popen(
                        [python_exec, process_script, '--process-one', str(album_id)],
                        cwd=working_dir,
                        env=env,
                        stdout=lf,
                        stderr=lf,
                        start_new_session=True
                    )

                    # Best-effort background monitor to flip album to error if the process fails fast
                    def monitor_processing():
                        import subprocess as _subprocess
                        try:
                            exit_code = proc.wait(timeout=60)
                            if exit_code != 0:
                                conn_mon = get_db_connection()
                                cur_mon = conn_mon.cursor()
                                cur_mon.execute(
                                    "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                    (f"Processing failed with exit code {exit_code}", album_id)
                                )
                                conn_mon.commit()
                                conn_mon.close()
                                lf.write(f"Updated album {album_id} status to error (exit code: {exit_code})\n")
                        except _subprocess.TimeoutExpired:
                            # Normal for long-running jobs
                            pass
                        except Exception as monitor_error:
                            lf.write(f"Error monitoring process for album {album_id}: {monitor_error}\n")

                    import threading as _threading
                    monitor_thread = _threading.Thread(target=monitor_processing, daemon=True)
                    monitor_thread.start()

            except Exception as e:
                print(f"Warning: Could not trigger processing for album {album_id}: {e}")
        
        else:  # single mode
            # Save each file individually
            for file in files:
                if not file.filename:
                    continue
                
                filename = secure_filename(file.filename)
                unique_filename = get_unique_filename(get_files_dir(), filename)
                file_path = get_files_dir() / unique_filename
                
                file.save(str(file_path))
                
                # Determine media type
                media_type = determine_media_type(unique_filename)
                if not media_type:
                    continue
                
                # Compute file hash for duplicate detection
                file_hash = get_file_hash(str(file_path.resolve()))
                
                # Check for duplicates
                duplicate_id = None
                if file_hash:
                    cursor.execute(
                        "SELECT id, file_path FROM memes WHERE file_hash = ? LIMIT 1",
                        (file_hash,)
                    )
                    duplicate = cursor.fetchone()
                    if duplicate:
                        duplicate_id = duplicate[0]
                        duplicate_path = Path(duplicate[1]).name if duplicate[1] else "unknown"
                        
                        # Add as error with duplicate note
                        cursor.execute(
                            "INSERT INTO memes (file_path, media_type, status, file_hash, error_message) VALUES (?, ?, 'error', ?, ?)",
                            (str(file_path.resolve()), media_type, file_hash, f"Duplicate of meme {duplicate_id} ({duplicate_path})")
                        )
                        meme_id = cursor.lastrowid
                        meme_ids.append(meme_id)
                        continue
                
                # Add to database with status='new'
                cursor.execute(
                    "INSERT INTO memes (file_path, media_type, status, file_hash) VALUES (?, ?, 'new', ?)",
                    (str(file_path.resolve()), media_type, file_hash)
                )
                meme_id = cursor.lastrowid
                meme_ids.append(meme_id)
                
                # Trigger processing for this meme
                try:
                    instance_dir = get_script_dir()  # Instance directory
                    venv_dir = get_venv_dir()
                    
                    # Resolve process_memes.py location using helper scripts dir when provided
                    script_dir = app.config.get('HELPER_SCRIPTS_DIR', instance_dir)
                    process_script = os.path.join(script_dir, 'process_memes.py')
                    
                    log_file = os.path.join(get_log_dir(), 'scan.log')
                    os.makedirs(os.path.dirname(log_file), exist_ok=True)
                    
                    # Set up environment variables for the processing script
                    env = os.environ.copy()
                    env['SCRIPT_DIR'] = instance_dir  # Instance directory (for files, logs, etc.)
                    env['LOG_DIR'] = get_log_dir()
                    env['DB_PATH'] = get_db_path()
                    env['MEMES_DIR'] = get_memes_dir()
                    env['MEMES_URL_BASE'] = get_memes_url_base()  # Critical for Replicate API image URLs
                    env['VENV_DIR'] = venv_dir
                    
                    # Working directory should be where the process script is located
                    working_dir = script_dir
                    
                    with open(log_file, 'a', encoding='utf-8') as lf:
                        lf.write("================================\n")
                        lf.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Processing uploaded meme (id={meme_id})\n")
                        lf.write(f"Process script: {process_script}\n")
                        lf.write(f"Working dir: {working_dir}\n")
                        lf.write("================================\n")
                        
                        python_exec = os.path.join(venv_dir, "bin", "python") if os.path.exists(os.path.join(venv_dir, "bin", "python")) else "python3"
                        
                        # Start processing in background with error monitoring
                        proc = subprocess.Popen(
                            [python_exec, process_script, '--process-one', str(meme_id)],
                            cwd=working_dir,
                            env=env,
                            stdout=lf,
                            stderr=lf,
                            start_new_session=True
                        )
                        
                        # Start a background thread to monitor the process and update status on failure
                        def monitor_processing():
                            import threading
                            import time
                            try:
                                # Wait for process to complete (max 60 seconds)
                                exit_code = proc.wait(timeout=60)
                                if exit_code != 0:
                                    # Processing failed, update meme status to error
                                    conn = get_db_connection()
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                        (f"Processing failed with exit code {exit_code}", meme_id)
                                    )
                                    conn.commit()
                                    conn.close()
                                    
                                    # Re-open log file for writing error
                                    with open(log_file, 'a', encoding='utf-8') as thread_lf:
                                        thread_lf.write(f"Updated meme {meme_id} status to error (exit code: {exit_code})\n")
                            except subprocess.TimeoutExpired:
                                # Process is still running after 60 seconds, that's normal for processing
                                pass
                            except Exception as monitor_error:
                                try:
                                    with open(log_file, 'a', encoding='utf-8') as thread_lf:
                                        thread_lf.write(f"Error monitoring process for meme {meme_id}: {monitor_error}\n")
                                except:
                                    pass
                        
                        # Start monitoring in a separate thread
                        import threading
                        monitor_thread = threading.Thread(target=monitor_processing, daemon=True)
                        monitor_thread.start()
                        
                except Exception as e:
                    print(f"Warning: Could not trigger processing for meme {meme_id}: {e}")
                    # If we can't even start processing, mark as error immediately
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE memes SET status='error', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (f"Failed to start processing: {str(e)}", meme_id)
                        )
                        conn.commit()
                        conn.close()
                    except Exception as db_error:
                        print(f"Could not update meme {meme_id} status: {db_error}")
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'meme_ids': meme_ids,
            'count': len(meme_ids)
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host=get_host(), port=get_port(), debug=False)
