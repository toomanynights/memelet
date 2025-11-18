#!/usr/bin/env python3
"""
Configuration loader for Memelet.
Supports both standalone and multi-tenant deployments without knowing about multi-tenancy.

Priority:
1. Flask app.config (for multi-tenant wrappers)
2. Environment variables (for standalone)
3. Defaults
"""
import os
from pathlib import Path
from flask import current_app, has_app_context

# Load .env file if it exists (for standalone installations)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # python-dotenv not installed, skip
    pass

def get_config_value(key, default=None):
    """Get config value from app.config (if available) or environment variables"""
    # Check Flask app.config first (for multi-tenant wrapper)
    if has_app_context():
        try:
            value = current_app.config.get(key)
            if value is not None:
                return value
        except RuntimeError:
            pass
    
    # Fall back to environment variable
    return os.environ.get(key, default)

def get_install_dir():
    """Get installation directory (where this script is located)"""
    return Path(__file__).parent.resolve()

def get_memes_dir():
    """Get directory where meme files are stored"""
    return get_config_value('MEMES_DIR', str(get_install_dir() / 'files'))

def get_db_path():
    """Get path to SQLite database file"""
    return get_config_value('DB_PATH', str(get_install_dir() / 'memelet.db'))

def get_log_dir():
    """Get directory for log files"""
    return get_config_value('LOG_DIR', str(get_install_dir() / 'logs'))

def get_base_url():
    """Get base URL for this Memelet instance"""
    return get_config_value('BASE_URL', 'http://localhost:5000')

def get_memes_url_base():
    """Get base URL for serving meme files"""
    return get_config_value('MEMES_URL_BASE', f'{get_base_url()}/files/')

def get_host():
    """Get host to bind Flask server to"""
    return get_config_value('HOST', '127.0.0.1')

def get_port():
    """Get port to bind Flask server to"""
    return int(get_config_value('PORT', '5000'))

def get_timezone():
    """Get timezone for this instance"""
    return get_config_value('TZ', 'UTC')

def get_script_dir():
    """Get script directory (for shell scripts and background processes)"""
    return get_config_value('SCRIPT_DIR', str(get_install_dir()))

def get_venv_dir():
    """Get virtual environment directory"""
    return get_config_value('VENV_DIR', str(get_install_dir() / 'venv'))

