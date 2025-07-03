import os
import subprocess
import re
from datetime import datetime
import pytz
from flask import request, jsonify
from .config import *
from .auth import login_required
from .sessions import load_sessions, save_sessions

jakarta_tz = pytz.timezone('Asia/Jakarta')

def init_streaming(app, socketio, scheduler):
    """Initialize streaming module"""
    
    @app.route('/api/start', methods=['POST'])
    @login_required
    def start_streaming_api():
        try:
            data = request.get_json()
            platform = data.get('platform')
            stream_key = data.get('stream_key')
            video_file = data.get('video_file')
            session_name = data.get('session_name')
            
            if not all([platform, stream_key, video_file, session_name]):
                return jsonify({'success': False, 'message': 'All fields are required'}), 400
            
            # Validate video file exists
            video_path = os.path.join(VIDEOS_DIR, video_file)
            if not os.path.exists(video_path):
                return jsonify({'success': False, 'message': 'Video file not found'}), 404
            
            # Create and start streaming service
            session_id = create_streaming_session(platform, stream_key, video_file, session_name)
            
            if session_id:
                # Emit update to frontend
                socketio.emit('sessions_update', get_active_sessions_data())
                return jsonify({'success': True, 'message': 'Streaming started successfully', 'session_id': session_id})
            else:
                return jsonify({'success': False, 'message': 'Failed to start streaming'}), 500
                
        except Exception as e:
            logger.error(f"Error starting streaming: {e}")
            return jsonify({'success': False, 'message': 'Failed to start streaming'}), 500
    
    @app.route('/api/stop', methods=['POST'])
    @login_required
    def stop_streaming_api():
        try:
            data = request.get_json()
            session_id = data.get('session_id')
            
            if not session_id:
                return jsonify({'success': False, 'message': 'Session ID required'}), 400
            
            if stop_streaming_session(session_id):
                # Emit update to frontend
                socketio.emit('sessions_update', get_active_sessions_data())
                return jsonify({'success': True, 'message': 'Streaming stopped successfully'})
            else:
                return jsonify({'success': False, 'message': 'Failed to stop streaming'}), 500
                
        except Exception as e:
            logger.error(f"Error stopping streaming: {e}")
            return jsonify({'success': False, 'message': 'Failed to stop streaming'}), 500

def sanitize_service_name(name):
    """Sanitize name for systemd service"""
    # Replace non-alphanumeric characters with hyphens
    sanitized = re.sub(r'[^\w-]', '-', str(name))
    # Remove multiple consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    # Limit length
    return sanitized[:50]

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

def create_streaming_session(platform, stream_key, video_file, session_name):
    """Create a new streaming session"""
    try:
        # Generate session ID
        session_id = sanitize_service_name(session_name)
        
        # Get platform URL
        if platform == 'YouTube':
            rtmp_url = 'rtmp://a.rtmp.youtube.com/live2'
        elif platform == 'Facebook':
            rtmp_url = 'rtmps://live-api-s.facebook.com:443/rtmp'
        else:
            logger.error(f"Unsupported platform: {platform}")
            return None
        
        # Create systemd service
        if not create_systemd_service(session_id, video_file, rtmp_url, stream_key):
            return None
        
        # Save session data
        sessions_data = load_sessions()
        sessions_data['active_sessions'][session_id] = {
            'username': session.get('username', 'Unknown'),
            'video_file': video_file,
            'platform': platform,
            'stream_key': stream_key,
            'started_at': datetime.now(jakarta_tz).isoformat(),
            'status': 'active'
        }
        
        save_sessions(sessions_data)
        
        logger.info(f"Created streaming session: {session_id}")
        return session_id
        
    except Exception as e:
        logger.error(f"Error creating streaming session: {e}")
        return None

def stop_streaming_session(session_id):
    """Stop a streaming session"""
    try:
        # Stop systemd service
        if not stop_systemd_service(session_id):
            logger.warning(f"Failed to stop systemd service for {session_id}")
        
        # Update session data
        sessions_data = load_sessions()
        
        if session_id in sessions_data['active_sessions']:
            session_info = sessions_data['active_sessions'][session_id]
            session_info['stopped_at'] = datetime.now(jakarta_tz).isoformat()
            session_info['status'] = 'inactive'
            
            # Move to inactive sessions
            sessions_data['inactive_sessions'][session_id] = session_info
            del sessions_data['active_sessions'][session_id]
            
            save_sessions(sessions_data)
            
            logger.info(f"Stopped streaming session: {session_id}")
            return True
        else:
            logger.warning(f"Session {session_id} not found in active sessions")
            return False
            
    except Exception as e:
        logger.error(f"Error stopping streaming session: {e}")
        return False

# Import required functions
from .sessions import get_active_sessions_data
from flask import session