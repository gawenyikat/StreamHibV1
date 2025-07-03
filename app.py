import os
import json
import subprocess
import hashlib
import uuid
from datetime import datetime, timedelta
import pytz
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from filelock import FileLock
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
import logging

# Import modules
from modules.config import *
from modules.auth import *
from modules.sessions import *
from modules.videos import *
from modules.streaming import *
from modules.scheduler import *
from modules.domain import *
from modules.recovery import *
from modules.admin import *

app = Flask(__name__)
app.secret_key = 'streamhib_v2_secret_key_2025'
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = BackgroundScheduler()

# Initialize modules with app context
init_auth(app)
init_sessions(app, socketio)
init_videos(app, socketio)
init_streaming(app, socketio, scheduler)
init_scheduler_module(app, socketio, scheduler)
init_domain(app, socketio)
init_recovery(app, socketio, scheduler)
init_admin(app, socketio)

# Routes
@app.route('/')
def index():
    if not is_customer_logged_in():
        return redirect(url_for('customer_login'))
    return render_template('index.html')

@app.route('/login')
def customer_login():
    if is_customer_logged_in():
        return redirect(url_for('index'))
    return render_template('customer_login.html')

@app.route('/register')
def customer_register():
    if is_customer_logged_in():
        return redirect(url_for('index'))
    
    # Check if any users exist (only allow one user)
    users = load_users()
    if users:
        return render_template('registration_closed.html')
    
    return render_template('customer_register.html')

@app.route('/logout')
def customer_logout():
    session.clear()
    return redirect(url_for('customer_login'))

# Initialize scheduler
def init_scheduler():
    """Initialize the background scheduler"""
    try:
        # Add recovery job - runs every 5 minutes
        scheduler.add_job(
            func=recovery_orphaned_sessions,
            trigger='interval',
            minutes=5,
            id='recovery_job',
            replace_existing=True
        )
        
        scheduler.start()
        logger.info("SCHEDULER: Background scheduler started")
        
    except Exception as e:
        logger.error(f"SCHEDULER: Error starting scheduler: {e}")

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    os.makedirs('static', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    # Initialize scheduler
    init_scheduler()
    
    # Run the app
    logger.info("StreamHib V2 starting...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)