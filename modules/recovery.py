import os
import subprocess
from datetime import datetime, timedelta
import pytz
from flask import request, jsonify
from .config import *
from .auth import admin_required
from .sessions import load_sessions, save_sessions, get_active_sessions_data
from .streaming import is_service_running, create_systemd_service, sanitize_service_name

jakarta_tz = pytz.timezone('Asia/Jakarta')

def init_recovery(app, socketio, scheduler):
    """Initialize recovery module"""
    
    @app.route('/api/recovery/manual', methods=['POST'])
    @admin_required
    def manual_recovery_api():
        try:
            logger.info("RECOVERY: Manual recovery triggered")
            
            # Run recovery
            recovery_result = recovery_orphaned_sessions()
            
            # Run cleanup
            cleanup_count = cleanup_unused_services()
            
            # Emit updates to frontend
            socketio.emit('sessions_update', get_active_sessions_data())
            
            return jsonify({
                'success': True,
                'message': 'Manual recovery completed',
                'recovery_result': recovery_result,
                'cleanup_count': cleanup_count
            })
            
        except Exception as e:
            logger.error(f"Manual recovery error: {e}")
            return jsonify({'success': False, 'message': f'Recovery failed: {str(e)}'})

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
                    session_info['stopped_at'] = datetime.now(jakarta_tz).isoformat()
                    session_info['stop_reason'] = 'Video file not found during recovery'
                    session_info['status'] = 'inactive'
                    sessions_data['inactive_sessions'][session_id] = session_info
                    del sessions_data['active_sessions'][session_id]
                    moved_to_inactive_count += 1
                    continue
                
                # Try to recover the session
                platform = session_info.get('platform')
                stream_key = session_info.get('stream_key')
                
                if not platform or not stream_key:
                    logger.error(f"RECOVERY: Session {session_id} missing platform or stream key")
                    continue
                
                # Get platform URL
                if platform == 'YouTube':
                    rtmp_url = 'rtmp://a.rtmp.youtube.com/live2'
                elif platform == 'Facebook':
                    rtmp_url = 'rtmps://live-api-s.facebook.com:443/rtmp'
                else:
                    logger.error(f"RECOVERY: Unsupported platform {platform} for session {session_id}")
                    continue
                
                # Recreate systemd service
                if create_systemd_service(session_id, video_file, rtmp_url, stream_key):
                    logger.info(f"RECOVERY: Successfully recovered session {session_id}")
                    
                    # Update session info
                    session_info['recovered_at'] = datetime.now(jakarta_tz).isoformat()
                    session_info['recovery_count'] = session_info.get('recovery_count', 0) + 1
                    
                    recovered_count += 1
                else:
                    logger.error(f"RECOVERY: Failed to recover session {session_id}")
                    # Move to inactive
                    session_info['stopped_at'] = datetime.now(jakarta_tz).isoformat()
                    session_info['stop_reason'] = 'Recovery failed'
                    session_info['status'] = 'inactive'
                    sessions_data['inactive_sessions'][session_id] = session_info
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
                            # Stop and remove service
                            subprocess.run(['systemctl', 'stop', f"{service_name}.service"], check=False)
                            subprocess.run(['systemctl', 'disable', f"{service_name}.service"], check=False)
                            
                            service_file = f"/etc/systemd/system/{service_name}.service"
                            if os.path.exists(service_file):
                                os.remove(service_file)
                            
                            cleanup_count += 1
        
        if cleanup_count > 0:
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
        
        logger.info(f"CLEANUP: Completed - Cleaned {cleanup_count} services")
        return cleanup_count
        
    except Exception as e:
        logger.error(f"CLEANUP: Error in cleanup process: {e}")
        return 0

def validate_session_data(session_data):
    """Validate session data for recovery"""
    required_fields = ['video_file', 'stream_key', 'platform']
    
    for field in required_fields:
        if not session_data.get(field):
            logger.error(f"VALIDATION: Missing field '{field}' in session data")
            return False
    
    # Validate platform
    if session_data.get('platform') not in ['YouTube', 'Facebook']:
        logger.error(f"VALIDATION: Invalid platform '{session_data.get('platform')}'")
        return False
    
    # Validate video file exists
    video_path = os.path.join(VIDEOS_DIR, session_data.get('video_file'))
    if not os.path.isfile(video_path):
        logger.error(f"VALIDATION: Video file '{session_data.get('video_file')}' not found")
        return False
    
    return True

def perform_startup_recovery():
    """Perform complete recovery on startup"""
    logger.info("=== STARTING STARTUP RECOVERY ===")
    
    try:
        # Recovery orphaned sessions
        recovery_orphaned_sessions()
        
        # Cleanup unused services
        cleanup_unused_services()
        
        logger.info("=== STARTUP RECOVERY COMPLETED ===")
        
    except Exception as e:
        logger.error(f"STARTUP RECOVERY: Error during recovery: {e}")