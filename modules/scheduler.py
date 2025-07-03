from datetime import datetime, timedelta
import pytz
from flask import request, jsonify
from .config import *
from .auth import login_required
from .streaming import create_streaming_session, stop_streaming_session

jakarta_tz = pytz.timezone('Asia/Jakarta')

def init_scheduler_module(app, socketio, scheduler):
    """Initialize scheduler module"""
    
    @app.route('/api/schedule', methods=['POST'])
    @login_required
    def schedule_streaming_api():
        try:
            data = request.get_json()
            platform = data.get('platform')
            stream_key = data.get('stream_key')
            video_file = data.get('video_file')
            session_name = data.get('session_name')
            start_time = data.get('start_time')
            duration = data.get('duration', 0)
            
            if not all([platform, stream_key, video_file, session_name, start_time]):
                return jsonify({'success': False, 'message': 'All fields are required'}), 400
            
            # Parse start time
            try:
                start_dt = datetime.fromisoformat(start_time).replace(tzinfo=jakarta_tz)
            except ValueError:
                return jsonify({'success': False, 'message': 'Invalid start time format'}), 400
            
            # Check if start time is in the future
            if start_dt <= datetime.now(jakarta_tz):
                return jsonify({'success': False, 'message': 'Start time must be in the future'}), 400
            
            # Schedule the streaming
            job_id = f"scheduled-{session_name}-{int(start_dt.timestamp())}"
            
            scheduler.add_job(
                func=start_scheduled_streaming,
                trigger='date',
                run_date=start_dt,
                args=[platform, stream_key, video_file, session_name, duration],
                id=job_id,
                replace_existing=True
            )
            
            # If duration is specified, schedule stop
            if duration > 0:
                stop_dt = start_dt + timedelta(minutes=duration)
                stop_job_id = f"stop-{job_id}"
                
                scheduler.add_job(
                    func=stop_scheduled_streaming,
                    trigger='date',
                    run_date=stop_dt,
                    args=[session_name],
                    id=stop_job_id,
                    replace_existing=True
                )
            
            # Save schedule info
            sessions_data = load_sessions()
            sessions_data['scheduled_sessions'][job_id] = {
                'session_name': session_name,
                'platform': platform,
                'stream_key': stream_key,
                'video_file': video_file,
                'start_time': start_dt.isoformat(),
                'duration': duration,
                'status': 'scheduled'
            }
            save_sessions(sessions_data)
            
            # Emit update to frontend
            socketio.emit('schedules_update', get_scheduled_sessions())
            
            return jsonify({'success': True, 'message': 'Streaming scheduled successfully'})
            
        except Exception as e:
            logger.error(f"Error scheduling streaming: {e}")
            return jsonify({'success': False, 'message': 'Failed to schedule streaming'}), 500

def start_scheduled_streaming(platform, stream_key, video_file, session_name, duration=0):
    """Start a scheduled streaming session"""
    try:
        logger.info(f"Starting scheduled streaming: {session_name}")
        
        # Create streaming session
        session_id = create_streaming_session(platform, stream_key, video_file, session_name)
        
        if session_id:
            logger.info(f"Scheduled streaming started successfully: {session_id}")
        else:
            logger.error(f"Failed to start scheduled streaming: {session_name}")
            
    except Exception as e:
        logger.error(f"Error in start_scheduled_streaming: {e}")

def stop_scheduled_streaming(session_name):
    """Stop a scheduled streaming session"""
    try:
        logger.info(f"Stopping scheduled streaming: {session_name}")
        
        # Find session by name and stop it
        sessions_data = load_sessions()
        session_id = None
        
        for sid, session_info in sessions_data['active_sessions'].items():
            if session_info.get('username') == session_name:
                session_id = sid
                break
        
        if session_id:
            stop_streaming_session(session_id)
            logger.info(f"Scheduled streaming stopped successfully: {session_id}")
        else:
            logger.warning(f"Could not find active session for: {session_name}")
            
    except Exception as e:
        logger.error(f"Error in stop_scheduled_streaming: {e}")

def get_scheduled_sessions():
    """Get list of scheduled sessions"""
    try:
        sessions_data = load_sessions()
        scheduled = sessions_data.get('scheduled_sessions', {})
        
        # Convert to list format for frontend
        sessions_list = []
        for job_id, session_info in scheduled.items():
            sessions_list.append({
                'id': job_id,
                'session_name': session_info.get('session_name'),
                'platform': session_info.get('platform'),
                'video_file': session_info.get('video_file'),
                'start_time': session_info.get('start_time'),
                'duration': session_info.get('duration'),
                'status': session_info.get('status')
            })
        
        return sessions_list
    except Exception as e:
        logger.error(f"Error getting scheduled sessions: {e}")
        return []

# Import required functions
from .sessions import load_sessions, save_sessions