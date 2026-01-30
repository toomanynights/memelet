#!/usr/bin/env python3
"""
Unified version checking for Memelet.
Single-tenant first design with multi-tenant override via environment variables.

Version Management:
- Versions and branches stored in local database
- Checks GitHub releases for updates (all modes)
- Multi-tenant overrides branch via MEMELET_BRANCH env var
- Supports dev, beta, and main branches with separate releases

GitHub Release Format:
- main branch: v1.2, v1.3, etc.
- beta branch: beta-v1.2, beta-v1.3, etc.
- dev branch: dev-v1.2, dev-v1.3, etc.
"""
import os
import requests
from pathlib import Path
import subprocess
import sys


# GitHub repository configuration
GITHUB_REPO = 'toomanynights/memelet'


def get_db_connection():
    """Get database connection from app"""
    # Import here to avoid circular dependency
    from config import get_db_path
    import sqlite3
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key, default=None):
    """Get a setting from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row['value'] if row else default
    except Exception:
        return default


def set_setting(key, value):
    """Set a setting in the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error setting {key}: {e}")
        return False


def get_current_branch():
    """
    Get current branch.
    Multi-tenant: from MEMELET_BRANCH env var (can't change)
    Single-tenant: from database (user can change)
    """
    # Multi-tenant override via environment variable
    env_branch = os.environ.get('MEMELET_BRANCH')
    if env_branch:
        return env_branch
    
    # Single-tenant: read from database
    return get_setting('current_branch', 'main')


def can_switch_branch():
    """
    Check if user can switch branches.
    Returns False if branch is set via environment variable (multi-tenant).
    """
    return 'MEMELET_BRANCH' not in os.environ


def set_current_branch(branch):
    """Set current branch (only works if not overridden by env var)"""
    if not can_switch_branch():
        return False
    return set_setting('current_branch', branch)


def get_current_version():
    """Get current running version from database"""
    return get_setting('current_version', 'v1.0')


def set_current_version(version):
    """Set current version in database"""
    return set_setting('current_version', version)


def get_available_version():
    """Get latest available version from database cache"""
    return get_setting('available_version', get_current_version())


def get_release_notes():
    """Get cached release notes from database"""
    return get_setting('release_notes', '')


def check_github_for_updates():
    """
    Check GitHub for latest release on current branch.
    Updates database cache with latest version and release notes.
    
    Release naming convention:
    - main branch: v1.2, v1.3, etc.
    - beta branch: beta-v1.2, beta-v1.3, etc.
    - dev branch: dev-v1.2, dev-v1.3, etc.
    """
    branch = get_current_branch()
    
    try:
        # Fetch all releases from GitHub
        response = requests.get(
            f'https://api.github.com/repos/{GITHUB_REPO}/releases',
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"GitHub API returned {response.status_code}")
            return False
        
        releases = response.json()
        
        # Find latest release for current branch
        # main branch: look for tags like "v1.2" (no prefix)
        # beta branch: look for tags like "beta-v1.2"
        # dev branch: look for tags like "dev-v1.2"
        prefix = '' if branch == 'main' else f'{branch}-'
        
        for release in releases:
            tag = release.get('tag_name', '')
            
            # Check if this release matches our branch
            if branch == 'main':
                # Main branch: tag should be like "v1.2" without any prefix
                if not tag.startswith(('dev-', 'beta-')) and tag.startswith('v'):
                    # Found matching release!
                    available_version = tag
                    notes = release.get('body', '')
                    
                    # Update database cache
                    set_setting('available_version', available_version)
                    set_setting('release_notes', notes)
                    set_setting('last_update_check', str(int(Path(__file__).stat().st_mtime)))
                    return True
            else:
                # Beta or dev branch: tag should start with branch prefix
                if tag.startswith(prefix):
                    # Found matching release!
                    available_version = tag
                    notes = release.get('body', '')
                    
                    # Update database cache
                    set_setting('available_version', available_version)
                    set_setting('release_notes', notes)
                    set_setting('last_update_check', str(int(Path(__file__).stat().st_mtime)))
                    return True
        
        # No matching release found for this branch
        print(f"No release found for branch '{branch}' with prefix '{prefix}'")
        return False
        
    except Exception as e:
        print(f"Error checking GitHub: {e}")
        return False


def check_for_updates():
    """
    Check if updates are available.
    Returns dict with update info.
    """
    # Refresh from GitHub
    check_github_for_updates()
    
    # Get cached values
    branch = get_current_branch()
    current = get_current_version()
    available = get_available_version()
    notes = get_release_notes()
    
    return {
        'current_branch': branch,
        'current_version': current,
        'latest_version': available,
        'update_available': available != current,
        'release_notes': notes,
        'can_switch_branch': can_switch_branch()
    }


def request_update():
    """
    Perform update.
    Single-tenant: git pull + pip install
    Multi-tenant: request from Memelord (will be intercepted by wrapper)
    """
    branch = get_current_branch()
    available_version = get_available_version()
    
    try:
        install_dir = Path(__file__).parent
        
        # Step 1: Git fetch and checkout branch
        print(f"Fetching updates from {branch} branch...")
        result = subprocess.run(
            ['git', 'fetch', 'origin', branch],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Git fetch failed: {result.stderr}',
                'details': result.stdout
            }
        
        # Step 2: Checkout branch
        result = subprocess.run(
            ['git', 'checkout', branch],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Git checkout failed: {result.stderr}',
                'details': result.stdout
            }
        
        # Step 3: Pull latest
        result = subprocess.run(
            ['git', 'pull', 'origin', branch],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Git pull failed: {result.stderr}',
                'details': result.stdout
            }
        
        git_output = result.stdout.strip()
        
        # Check if already up to date
        if 'Already up to date' in git_output or 'Already up-to-date' in git_output:
            return {
                'success': True,
                'message': 'Already up to date! No update needed.',
                'already_updated': True
            }
        
        # Step 4: Install/update requirements
        print("Installing requirements...")
        result = subprocess.run(
            [sys.executable, 'install', '-r', 'requirements.txt', '--upgrade'],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if result.returncode != 0:
            return {
                'success': False,
                'error': f'Pip install failed: {result.stderr}',
                'details': result.stdout
            }
        
        pip_output = result.stdout.strip()
        
        # Step 5: Mark as updated
        set_current_version(available_version)
        
        # Success!
        return {
            'success': True,
            'message': '✓ Update downloaded and installed!',
            'restart_needed': True,
            'restart_instructions': [
                'Restart Memelet to apply the update:',
                '',
                'For systemd service:',
                '  sudo systemctl restart memelet',
                '',
                'For manual run:',
                '  Stop the current process (Ctrl+C)',
                '  Re-run: python3 app.py',
                '',
                'For Docker:',
                '  docker-compose restart memelet'
            ],
            'git_output': git_output,
            'pip_output': pip_output,
            'new_version': available_version
        }
        
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Update timed out. Please try again or update manually.'
        }
    except FileNotFoundError as e:
        if 'git' in str(e).lower():
            return {
                'success': False,
                'error': 'Git not found. Please install git or update manually.'
            }
        raise
    except Exception as e:
        return {
            'success': False,
            'error': f'Update failed: {str(e)}'
        }


def send_session_heartbeat(session_id):
    """
    Send session heartbeat (for multi-tenant session tracking).
    This is a no-op for single-tenant.
    Multi-tenant will override this via wrapper if needed.
    """
    # In single-tenant mode, this does nothing
    # Multi-tenant can hook this if needed
    pass
