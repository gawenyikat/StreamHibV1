import hashlib
from flask import session, request, jsonify, redirect, url_for
from functools import wraps
from .config import *

def init_auth(app):
    """Initialize authentication module"""
    pass

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    """Verify password against hash"""
    return hash_password(password) == hashed

def is_admin_logged_in():
    """Check if admin is logged in"""
    return session.get('admin_logged_in', False)

def is_customer_logged_in():
    """Check if customer is logged in"""
    return session.get('customer_logged_in', False) and session.get('username')

def load_users():
    """Load users data"""
    return load_json_file(USERS_FILE, {})

def save_users(users_data):
    """Save users data"""
    return save_json_file(USERS_FILE, users_data, users_lock)

def login_required(f):
    """Decorator for routes that require customer login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_customer_logged_in():
            return redirect(url_for('customer_login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator for routes that require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_logged_in():
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function