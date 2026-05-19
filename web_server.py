"""
Flask Web Server for Knowledge Graph Visualization
"""
import os
import json
import time
import secrets
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from threading import Thread
from knowledge_graph_service import KnowledgeGraphService
from config import WEB_HOST, WEB_PORT, WEB_DEBUG

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global service instance
kg_service = None


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')


@app.route('/api/stats')
def get_stats():
    """Get current statistics"""
    if kg_service:
        return jsonify(kg_service.get_statistics())
    return jsonify({})


@app.route('/api/graph')
def get_graph():
    """Get graph data for visualization"""
    if kg_service:
        return jsonify(kg_service.get_graph_data())
    return jsonify({'entities': [], 'relationships': [], 'files': [], 'dependencies': []})


@app.route('/api/search')
def search():
    """Search entities"""
    query = request.args.get('q', '')
    if kg_service and query and query.strip():
        results = kg_service.search(query)
        return jsonify(results)
    return jsonify([])


@app.route('/api/events')
def get_events():
    """Get recent events"""
    if kg_service:
        events = kg_service.db.get_recent_events(limit=50)
        return jsonify(events)
    return jsonify([])


@app.route('/api/entity/<int:entity_id>')
def get_entity(entity_id):
    """Get entity details"""
    if kg_service:
        entity = kg_service.db.get_entity_details(entity_id)
        return jsonify(entity) if entity else jsonify({'error': 'Not found'}), 404
    return jsonify({'error': 'Service not available'}), 503


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    emit('connected', {'data': 'Connected to Knowledge Graph Service'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')


def broadcast_update(file_path: str, stats: dict):
    """Broadcast update to all connected clients"""
    socketio.emit('file_updated', {
        'file_path': file_path,
        'stats': stats,
        'timestamp': time.time()
    })


def run_server(service: KnowledgeGraphService, host: str = None, port: int = None):
    """Run the Flask server"""
    global kg_service
    kg_service = service
    
    # H-7: 注册实时推送回调，文件更新后自动通知所有客户端
    service.on_update_callback = broadcast_update
    
    host = host or WEB_HOST
    port = port or WEB_PORT
    
    print(f"Starting web server on http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=WEB_DEBUG, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    # For testing only
    service = KnowledgeGraphService()
    run_server(service)
