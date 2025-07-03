import os
import json
import logging
from filelock import FileLock

logger = logging.getLogger(__name__)

# File paths
SESSIONS_FILE = 'sessions.json'
USERS_FILE = 'users.json'
DOMAIN_CONFIG_FILE = 'domain_config.json'
VIDEOS_DIR = 'videos'

# File locks
sessions_lock = FileLock(f"{SESSIONS_FILE}.lock")
users_lock = FileLock(f"{USERS_FILE}.lock")
domain_lock = FileLock(f"{DOMAIN_CONFIG_FILE}.lock")

# Admin credentials
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'streamhib2025'

def load_json_file(file_path, default_data=None):
    """Load JSON file with error handling"""
    if default_data is None:
        default_data = {}
    
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        return default_data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading {file_path}: {e}")
        return default_data

def save_json_file(file_path, data, lock):
    """Save JSON file with file locking"""
    try:
        with lock:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving {file_path}: {e}")
        return False