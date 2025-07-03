import os
import json
from datetime import datetime
import pytz
from .config import *

jakarta_tz = pytz.timezone('Asia/Jakarta')

def init_sessions(app, socketio):
    """Initialize sessions module"""
    
    @app.route('/api/sessions', methods=['GET'])
    @login_required
    def list_sessions_api():
        try:
            return jsonify(get_active_sessions_data())
        except Exception as e:
            logger.error(f"Error API /api/sessions: {str(e)}", exc_info=True)
            return jsonify({'status': 'error', 'message': 'Gagal ambil sesi aktif.'}), 500

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

def get_active_sessions_data():
    """Get active sessions data"""
    try:
        sessions_data = load_sessions()
        active_sessions = sessions_data.get('active_sessions', {})
        
        # Convert to list format for frontend
        sessions_list = []
        for session_id, session_info in active_sessions.items():
            sessions_list.append({
                'id': session_id,
                'name': session_info.get('username', 'Unknown'),
                'video_file': session_info.get('video_file', 'Unknown'),
                'platform': session_info.get('platform', 'Unknown'),
                'stream_key': session_info.get('stream_key', 'Unknown'),
                'started_at': session_info.get('started_at', 'Unknown'),
                'status': session_info.get('status', 'active')
            })
        
        return sessions_list
    except Exception as e:
        logger.error(f"Error getting active sessions: {e}")
        return []

def get_inactive_sessions_data():
    """Get inactive sessions data"""
    try:
        sessions_data = load_sessions()
        inactive_sessions = sessions_data.get('inactive_sessions', {})
        
        # Convert to list format for frontend
        sessions_list = []
        for session_id, session_info in inactive_sessions.items():
            sessions_list.append({
                'id': session_id,
                'name': session_info.get('username', 'Unknown'),
                'video_file': session_info.get('video_file', 'Unknown'),
                'platform': session_info.get('platform', 'Unknown'),
                'stream_key': session_info.get('stream_key', 'Unknown'),
                'started_at': session_info.get('started_at', 'Unknown'),
                'stopped_at': session_info.get('stopped_at', 'Unknown'),
                'status': session_info.get('status', 'inactive')
            })
        
        return sessions_list
    except Exception as e:
        logger.error(f"Error getting inactive sessions: {e}")
        return []

# Import login_required from auth module
from .auth import login_required