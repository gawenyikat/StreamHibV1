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

app = Flask(__name__)
app.secret_key = 'streamhib_v2_secret_key_2025'
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app)

# Setup logging
logging.basicConfig(level=logging.INFO)
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

# Scheduler
scheduler = BackgroundScheduler()

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

def load_sessions():
    """Load sessions data"""
    return load_json_file(SESSIONS_FILE, {
        'active_sessions': {},
        'inactive_sessions': {},
        'scheduled_sessions': {}
    })

def save_sessions(sessions_data):
    """Save sessions data"""
    return save_json_file(SESSIONS_FILE, sessions_data, sessions_lock)

def load_users():
    """Load users data"""
    return load_json_file(USERS_FILE, {})

def save_users(users_data):
    """Save users data"""
    return save_json_file(USERS_FILE, users_data, users_lock)

def load_domain_config():
    """Load domain configuration"""
    return load_json_file(DOMAIN_CONFIG_FILE, {})

def save_domain_config(domain_data):
    """Save domain configuration"""
    return save_json_file(DOMAIN_CONFIG_FILE, domain_data, domain_lock)

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

def get_video_files():
    """Get list of video files"""
    if not os.path.exists(VIDEOS_DIR):
        os.makedirs(VIDEOS_DIR)
        return []
    
    video_extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']
    video_files = []
    
    for file in os.listdir(VIDEOS_DIR):
        if any(file.lower().endswith(ext) for ext in video_extensions):
            video_files.append(file)
    
    return sorted(video_files)

def create_systemd_service(session_id, video_file, rtmp_url, stream_key):
    """Create systemd service for streaming"""
    try:
        service_name = f"stream-{session_id}"
        service_file = f"/etc/systemd/system/{service_name}.service"
        
        video_path = os.path.join(os.getcwd(), VIDEOS_DIR, video_file)
        full_rtmp_url = f"{rtmp_url}/{stream_key}"
        
        service_content = f"""[Unit]
Description=StreamHib Session {session_id}
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/ffmpeg -re -stream_loop -1 -i "{video_path}" -c:v libx264 -preset veryfast -maxrate 3000k -bufsize 6000k -pix_fmt yuv420p -g 50 -c:a aac -b:a 160k -ac 2 -ar 44100 -f flv "{full_rtmp_url}"
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
"""
        
        with open(service_file, 'w') as f:
            f.write(service_content)
        
        # Reload systemd and start service
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', service_name], check=True)
        subprocess.run(['systemctl', 'start', service_name], check=True)
        
        logger.info(f"SYSTEMD: Created and started service {service_name}")
        return True
        
    except Exception as e:
        logger.error(f"SYSTEMD: Error creating service for {session_id}: {e}")
        return False

def stop_systemd_service(session_id):
    """Stop and remove systemd service"""
    try:
        service_name = f"stream-{session_id}"
        service_file = f"/etc/systemd/system/{service_name}.service"
        
        # Stop and disable service
        subprocess.run(['systemctl', 'stop', service_name], check=False)
        subprocess.run(['systemctl', 'disable', service_name], check=False)
        
        # Remove service file
        if os.path.exists(service_file):
            os.remove(service_file)
        
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        
        logger.info(f"SYSTEMD: Stopped and removed service {service_name}")
        return True
        
    except Exception as e:
        logger.error(f"SYSTEMD: Error stopping service for {session_id}: {e}")
        return False

def is_service_running(session_id):
    """Check if systemd service is running"""
    try:
        service_name = f"stream-{session_id}"
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            text=True
        )
        return result.stdout.strip() == 'active'
    except Exception as e:
        logger.error(f"SYSTEMD: Error checking service {session_id}: {e}")
        return False

def recovery_orphaned_sessions():
    """Recovery function for orphaned sessions"""
    try:
        logger.info("RECOVERY: Starting orphaned session recovery...")
        
        sessions_data = load_sessions()
        active_sessions = sessions_data.get('active_sessions', {})
        
        if not active_sessions:
            logger.info("RECOVERY: No active sessions to check")
            return {'recovered': 0, 'moved_to_inactive': 0, 'total_active': 0}
        
        recovered_count = 0
        moved_to_inactive_count = 0
        
        for session_id, session_info in list(active_sessions.items()):
            try:
                # Check if service is running
                if is_service_running(session_id):
                    logger.info(f"RECOVERY: Session {session_id} service is running - OK")
                    continue
                
                logger.warning(f"RECOVERY: Found orphaned session {session_id}")
                
                # Check if video file exists
                video_file = session_info.get('video_file')
                if not video_file:
                    logger.error(f"RECOVERY: Session {session_id} has no video file")
                    continue
                
                video_path = os.path.join(VIDEOS_DIR, video_file)
                if not os.path.exists(video_path):
                    logger.error(f"RECOVERY: Video file not found for session {session_id}: {video_file}")
                    # Move to inactive
                    sessions_data['inactive_sessions'][session_id] = session_info
                    sessions_data['inactive_sessions'][session_id]['stopped_at'] = datetime.now().isoformat()
                    sessions_data['inactive_sessions'][session_id]['stop_reason'] = 'Video file not found during recovery'
                    del sessions_data['active_sessions'][session_id]
                    moved_to_inactive_count += 1
                    continue
                
                # Try to recover the session
                rtmp_url = session_info.get('rtmp_url')
                stream_key = session_info.get('stream_key')
                
                if not rtmp_url or not stream_key:
                    logger.error(f"RECOVERY: Session {session_id} missing RTMP info")
                    continue
                
                # Recreate systemd service
                if create_systemd_service(session_id, video_file, rtmp_url, stream_key):
                    logger.info(f"RECOVERY: Successfully recovered session {session_id}")
                    
                    # Update session info
                    sessions_data['active_sessions'][session_id]['recovered_at'] = datetime.now().isoformat()
                    sessions_data['active_sessions'][session_id]['recovery_count'] = session_info.get('recovery_count', 0) + 1
                    
                    recovered_count += 1
                else:
                    logger.error(f"RECOVERY: Failed to recover session {session_id}")
                    # Move to inactive
                    sessions_data['inactive_sessions'][session_id] = session_info
                    sessions_data['inactive_sessions'][session_id]['stopped_at'] = datetime.now().isoformat()
                    sessions_data['inactive_sessions'][session_id]['stop_reason'] = 'Recovery failed'
                    del sessions_data['active_sessions'][session_id]
                    moved_to_inactive_count += 1
                    
            except Exception as e:
                logger.error(f"RECOVERY: Error processing session {session_id}: {e}")
        
        # Save updated sessions
        save_sessions(sessions_data)
        
        total_active = len(sessions_data.get('active_sessions', {}))
        
        logger.info(f"RECOVERY: Completed - Recovered: {recovered_count}, Moved to inactive: {moved_to_inactive_count}, Total active: {total_active}")
        
        return {
            'recovered': recovered_count,
            'moved_to_inactive': moved_to_inactive_count,
            'total_active': total_active
        }
        
    except Exception as e:
        logger.error(f"RECOVERY: Error in recovery process: {e}")
        return {'recovered': 0, 'moved_to_inactive': 0, 'total_active': 0}

def cleanup_unused_services():
    """Clean up systemd services that are not in active sessions"""
    try:
        logger.info("CLEANUP: Starting cleanup of unused services...")
        
        sessions_data = load_sessions()
        active_sessions = sessions_data.get('active_sessions', {})
        active_session_ids = set(active_sessions.keys())
        
        # Get all stream services
        result = subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--all', '--no-pager'],
            capture_output=True,
            text=True
        )
        
        cleanup_count = 0
        
        for line in result.stdout.split('\n'):
            if 'stream-' in line and '.service' in line:
                # Extract service name
                parts = line.split()
                if parts:
                    service_name = parts[0]
                    if service_name.endswith('.service'):
                        service_name = service_name[:-8]  # Remove .service
                    
                    # Extract session ID
                    if service_name.startswith('stream-'):
                        session_id = service_name[7:]  # Remove 'stream-'
                        
                        if session_id not in active_session_ids:
                            logger.info(f"CLEANUP: Removing unused service {service_name}")
                            stop_systemd_service(session_id)
                            cleanup_count += 1
        
        logger.info(f"CLEANUP: Completed - Cleaned {cleanup_count} services")
        return cleanup_count
        
    except Exception as e:
        logger.error(f"CLEANUP: Error in cleanup process: {e}")
        return 0

def setup_nginx_domain(domain_name, ssl_enabled=False, port=5000):
    """Setup Nginx configuration for domain"""
    try:
        # Create Nginx config
        config_content = f"""server {{
    listen 80;
    server_name {domain_name};
    
    location / {{
        proxy_pass http://localhost:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}"""
        
        config_file = f"/etc/nginx/sites-available/{domain_name}"
        with open(config_file, 'w') as f:
            f.write(config_content)
        
        # Enable site
        symlink_path = f"/etc/nginx/sites-enabled/{domain_name}"
        if not os.path.exists(symlink_path):
            os.symlink(config_file, symlink_path)
        
        # Test and reload Nginx
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        
        logger.info(f"DOMAIN: Nginx configured for {domain_name}")
        
        # Setup SSL if requested
        if ssl_enabled:
            try:
                subprocess.run([
                    'certbot', '--nginx', '-d', domain_name, '--non-interactive', '--agree-tos', '--email', 'admin@localhost'
                ], check=True)
                logger.info(f"DOMAIN: SSL configured for {domain_name}")
                return True, "Domain and SSL configured successfully"
            except subprocess.CalledProcessError as e:
                logger.error(f"DOMAIN: SSL setup failed for {domain_name}: {e}")
                return True, "Domain configured successfully, but SSL setup failed"
        
        return True, "Domain configured successfully"
        
    except Exception as e:
        logger.error(f"DOMAIN: Error setting up domain {domain_name}: {e}")
        return False, f"Domain setup failed: {str(e)}"

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

@app.route('/admin/login')
def admin_login():
    if is_admin_logged_in():
        return redirect(url_for('admin_index'))
    return render_template('admin_login.html')

@app.route('/admin')
def admin_index():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    # Get stats
    sessions_data = load_sessions()
    users_data = load_users()
    domain_config = load_domain_config()
    video_files = get_video_files()
    
    stats = {
        'total_users': len(users_data),
        'active_sessions': len(sessions_data.get('active_sessions', {})),
        'inactive_sessions': len(sessions_data.get('inactive_sessions', {})),
        'scheduled_sessions': len(sessions_data.get('scheduled_sessions', {})),
        'total_videos': len(video_files)
    }
    
    return render_template('admin_index.html', 
                         stats=stats, 
                         sessions=sessions_data,
                         domain_config=domain_config)

@app.route('/admin/users')
def admin_users():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    users_data = load_users()
    return render_template('admin_users.html', users=users_data)

@app.route('/admin/domain')
def admin_domain():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    domain_config = load_domain_config()
    return render_template('admin_domain.html', domain_config=domain_config)

@app.route('/admin/recovery')
def admin_recovery():
    if not is_admin_logged_in():
        return redirect(url_for('admin_login'))
    
    return render_template('admin_recovery.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/logout')
def customer_logout():
    session.clear()
    return redirect(url_for('customer_login'))

# API Routes
@app.route('/api/customer/login', methods=['POST'])
def api_customer_login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password required'})
        
        users = load_users()
        
        if username not in users:
            return jsonify({'success': False, 'message': 'Invalid username or password'})
        
        user_info = users[username]
        if not verify_password(password, user_info['password']):
            return jsonify({'success': False, 'message': 'Invalid username or password'})
        
        session['customer_logged_in'] = True
        session['username'] = username
        
        return jsonify({'success': True, 'message': 'Login successful'})
        
    except Exception as e:
        logger.error(f"Customer login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed'})

@app.route('/api/customer/register', methods=['POST'])
def api_customer_register():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password required'})
        
        users = load_users()
        
        # Only allow one user
        if users:
            return jsonify({'success': False, 'message': 'Registration is closed'})
        
        if username in users:
            return jsonify({'success': False, 'message': 'Username already exists'})
        
        # Create user
        users[username] = {
            'password': hash_password(password),
            'created_at': datetime.now().isoformat(),
            'role': 'customer'
        }
        
        if save_users(users):
            return jsonify({'success': True, 'message': 'Registration successful'})
        else:
            return jsonify({'success': False, 'message': 'Failed to save user data'})
        
    except Exception as e:
        logger.error(f"Customer registration error: {e}")
        return jsonify({'success': False, 'message': 'Registration failed'})

@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return jsonify({'success': True, 'message': 'Admin login successful'})
        else:
            return jsonify({'success': False, 'message': 'Invalid admin credentials'})
        
    except Exception as e:
        logger.error(f"Admin login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed'})

@app.route('/api/domain/setup', methods=['POST'])
def api_domain_setup():
    try:
        if not is_admin_logged_in():
            return jsonify({'success': False, 'message': 'Admin access required'})
        
        data = request.get_json()
        domain_name = data.get('domain_name', '').strip()
        ssl_enabled = data.get('ssl_enabled', False)
        port = data.get('port', 5000)
        
        if not domain_name:
            return jsonify({'success': False, 'message': 'Domain name is required'})
        
        # Setup Nginx and SSL
        success, message = setup_nginx_domain(domain_name, ssl_enabled, port)
        
        if success:
            # Save domain configuration
            domain_config = {
                'domain_name': domain_name,
                'ssl_enabled': ssl_enabled,
                'port': port,
                'configured_at': datetime.now().isoformat()
            }
            
            if save_domain_config(domain_config):
                return jsonify({'success': True, 'message': message})
            else:
                return jsonify({'success': False, 'message': 'Domain setup completed but failed to save configuration'})
        else:
            return jsonify({'success': False, 'message': message})
        
    except Exception as e:
        logger.error(f"Domain setup error: {e}")
        return jsonify({'success': False, 'message': f'Domain setup failed: {str(e)}'})

@app.route('/api/recovery/manual', methods=['POST'])
def api_manual_recovery():
    try:
        if not is_admin_logged_in():
            return jsonify({'success': False, 'message': 'Admin access required'})
        
        # Run recovery
        recovery_result = recovery_orphaned_sessions()
        
        # Run cleanup
        cleanup_count = cleanup_unused_services()
        
        return jsonify({
            'success': True,
            'message': 'Manual recovery completed',
            'recovery_result': recovery_result,
            'cleanup_count': cleanup_count
        })
        
    except Exception as e:
        logger.error(f"Manual recovery error: {e}")
        return jsonify({'success': False, 'message': f'Recovery failed: {str(e)}'})

@app.route('/api/sessions/stop/<session_id>', methods=['POST'])
def api_stop_session(session_id):
    try:
        if not is_admin_logged_in():
            return jsonify({'success': False, 'message': 'Admin access required'})
        
        sessions_data = load_sessions()
        
        if session_id not in sessions_data.get('active_sessions', {}):
            return jsonify({'success': False, 'message': 'Session not found'})
        
        # Stop systemd service
        if stop_systemd_service(session_id):
            # Move to inactive
            session_info = sessions_data['active_sessions'][session_id]
            session_info['stopped_at'] = datetime.now().isoformat()
            session_info['stop_reason'] = 'Stopped by admin'
            
            sessions_data['inactive_sessions'][session_id] = session_info
            del sessions_data['active_sessions'][session_id]
            
            save_sessions(sessions_data)
            
            return jsonify({'success': True, 'message': 'Session stopped successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to stop session'})
        
    except Exception as e:
        logger.error(f"Stop session error: {e}")
        return jsonify({'success': False, 'message': f'Failed to stop session: {str(e)}'})

@app.route('/api/admin/users/<username>', methods=['DELETE'])
def api_delete_user(username):
    try:
        if not is_admin_logged_in():
            return jsonify({'success': False, 'message': 'Admin access required'})
        
        users = load_users()
        
        if username not in users:
            return jsonify({'success': False, 'message': 'User not found'})
        
        del users[username]
        
        if save_users(users):
            return jsonify({'success': True, 'message': 'User deleted successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to delete user'})
        
    except Exception as e:
        logger.error(f"Delete user error: {e}")
        return jsonify({'success': False, 'message': f'Failed to delete user: {str(e)}'})

@app.route('/api/videos')
def api_get_videos():
    try:
        if not is_customer_logged_in():
            return jsonify({'success': False, 'message': 'Login required'})
        
        video_files = get_video_files()
        return jsonify({'success': True, 'videos': video_files})
        
    except Exception as e:
        logger.error(f"Get videos error: {e}")
        return jsonify({'success': False, 'message': 'Failed to get videos'})

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