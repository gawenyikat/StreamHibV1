import os
import subprocess
import re
from flask import request, jsonify, send_from_directory
from .config import *
from .auth import login_required

def init_videos(app, socketio):
    """Initialize videos module"""
    
    @app.route('/api/videos', methods=['GET'])
    @login_required
    def list_videos_api():
        try:
            return jsonify(get_videos_list())
        except Exception as e:
            logger.error(f"Error API /api/videos: {str(e)}", exc_info=True)
            return jsonify({'status': 'error', 'message': 'Gagal ambil daftar video.'}), 500
    
    @app.route('/api/videos/delete', methods=['POST'])
    @login_required
    def delete_video_api():
        try:
            data = request.get_json()
            filename = data.get('filename')
            
            if not filename:
                return jsonify({'success': False, 'message': 'Filename required'}), 400
            
            video_path = os.path.join(VIDEOS_DIR, filename)
            if not os.path.exists(video_path):
                return jsonify({'success': False, 'message': 'File not found'}), 404
            
            os.remove(video_path)
            
            # Emit update to frontend
            socketio.emit('videos_update', get_videos_list())
            
            return jsonify({'success': True, 'message': f'Video {filename} deleted successfully'})
            
        except Exception as e:
            logger.error(f"Error deleting video: {e}")
            return jsonify({'success': False, 'message': 'Failed to delete video'}), 500
    
    @app.route('/videos/<filename>')
    @login_required
    def serve_video(filename):
        return send_from_directory(VIDEOS_DIR, filename)

def get_videos_list():
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

def extract_drive_id(url_or_id):
    """Extract Google Drive file ID from URL or return ID if already valid"""
    if not url_or_id:
        return None
    
    # If it's a Google Drive URL, extract ID
    if "drive.google.com" in url_or_id:
        patterns = [
            r'/file/d/([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
            r'/d/([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url_or_id)
            if match:
                return match.group(1)
    
    # If it looks like a valid ID, return it
    if re.match(r'^[a-zA-Z0-9_-]{20,}$', url_or_id):
        return url_or_id
    
    return None