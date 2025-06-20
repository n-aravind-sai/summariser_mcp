"""
SummarizePro - Flask Web Interface
Main application file with improved organization and error handling
"""

from flask import Flask, render_template, request, jsonify
import logging
import signal
import sys
import os
import atexit
from typing import Optional

# Import utilities
from utils.mcp_manager import MCPManager
from utils.helpers import setup_logging
from config import Config

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Setup logging
setup_logging(app.config['LOG_LEVEL'])
logger = logging.getLogger(__name__)

# Global MCP manager
mcp_manager: Optional[MCPManager] = None

def cleanup():
    """Cleanup function to properly shut down resources"""
    global mcp_manager
    logger.info("Starting cleanup process...")
    
    if mcp_manager:
        mcp_manager.cleanup()
        logger.info("MCP manager cleaned up")
    
    logger.info("Cleanup completed")

def signal_handler(signum, frame):
    """Handle interrupt signals"""
    logger.info(f"Received signal {signum}")
    cleanup()
    sys.exit(0)

def initialize_app():
    """Initialize the application and start MCP server"""
    global mcp_manager
    
    logger.info("Initializing SummarizePro application...")
    
    # Create MCP manager
    mcp_manager = MCPManager()
    
    # Start MCP server
    if not mcp_manager.start():
        logger.error("Failed to start MCP server")
        return False
    
    logger.info("Application initialized successfully")
    return True

# Routes
@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    """Handle summarization requests"""
    if not mcp_manager or not mcp_manager.is_healthy():
        logger.error("MCP server not available for summarization")
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
            
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        logger.info(f"Processing URL: {url}")
        
        # Call the summarize_website tool
        result = mcp_manager.call_tool("summarize_website", {"url": url}, timeout=120)
        
        if result is None:
            logger.error("Failed to get response from MCP server")
            return jsonify({"error": "Failed to get response from MCP server"}), 500
        
        if result.startswith("❌"):
            logger.warning(f"Summarization failed: {result}")
            return jsonify({"error": result}), 500
        
        logger.info("Summarization completed successfully")
        return jsonify({"result": result})
        
    except Exception as e:
        logger.error(f"Summarize error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    """Test endpoint"""
    if not mcp_manager or not mcp_manager.is_healthy():
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        result = mcp_manager.call_tool("ping")
        return jsonify({"result": result})
    except Exception as e:
        logger.error(f"Ping error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/tools', methods=['GET'])
def list_tools():
    """List all available tools"""
    if not mcp_manager or not mcp_manager.is_healthy():
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        tools = mcp_manager.list_tools()
        return jsonify({"tools": tools})
    except Exception as e:
        logger.error(f"List tools error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    status = {
        "web_server": "ok",
        "mcp_server": "ok" if mcp_manager and mcp_manager.is_healthy() else "error",
        "tools_available": len(mcp_manager.available_tools) if mcp_manager else 0
    }
    return jsonify(status)

@app.route('/search', methods=['POST'])
def search_summaries():
    """Search summaries endpoint"""
    if not mcp_manager or not mcp_manager.is_healthy():
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        data = request.get_json()
        keyword = data.get('keyword', '').strip()
        
        if not keyword:
            return jsonify({"error": "Keyword is required"}), 400
        
        result = mcp_manager.call_tool("search_summaries", {"keyword": keyword})
        return jsonify({"result": result})
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/summaries', methods=['GET'])
def get_all_summaries():
    """Get all summaries endpoint"""
    if not mcp_manager or not mcp_manager.is_healthy():
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        result = mcp_manager.call_tool("view_all_summaries")
        return jsonify({"result": result})
        
    except Exception as e:
        logger.error(f"Get summaries error: {e}")
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Register cleanup function
    atexit.register(cleanup)
    
    print("=" * 60)
    print("🚀 SUMMARIZEPRO - WEB INTERFACE")
    print("=" * 60)
    print("Using organized directory structure")
    print("=" * 60)
    
    # Initialize application
    if not initialize_app():
        print("❌ Failed to initialize application")
        sys.exit(1)
    
    print("✅ Application initialized successfully")
    print("🌐 Starting web server...")
    print(f"📍 Open: http://localhost:{app.config['PORT']}")
    print("=" * 60)
    
    try:
        app.run(
            debug=app.config['DEBUG'],
            host=app.config['HOST'],
            port=app.config['PORT'],
            use_reloader=False
        )
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        cleanup()