from flask import Flask, render_template, request, jsonify
import subprocess
import json
import asyncio
import sys
import os
from threading import Thread
import time
from typing import Optional, Dict, Any
import signal
import atexit

# Import the SimpleMCPClient from the summariser_client.py file
try:
    from summariser_client import SimpleMCPClient
except ImportError:
    print("❌ Could not import SimpleMCPClient. Make sure summariser_client.py is in the same directory.")
    sys.exit(1)

app = Flask(__name__)

# Global variables to manage MCP client
mcp_process: Optional[subprocess.Popen] = None
mcp_client: Optional[SimpleMCPClient] = None
event_loop: Optional[asyncio.AbstractEventLoop] = None
loop_thread: Optional[Thread] = None

def start_event_loop():
    """Start the asyncio event loop in a separate thread"""
    global event_loop
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    event_loop.run_forever()

def run_async(coro):
    """Run an async function in the event loop thread"""
    if event_loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(coro, event_loop)
    return future.result(timeout=60)  # 60 second timeout

def start_mcp_server() -> bool:
    """Start the MCP server process and initialize client"""
    global mcp_process, mcp_client, loop_thread, event_loop
    
    try:
        # Start event loop thread
        print("[WEB] Starting event loop thread...")
        loop_thread = Thread(target=start_event_loop, daemon=True)
        loop_thread.start()
        time.sleep(1)  # Give thread time to start
        
        print("[WEB] Starting MCP server...")
        mcp_process = subprocess.Popen(
            [sys.executable, "summariser_server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            universal_newlines=False
        )
        
        # Give server time to start
        time.sleep(3)
        
        if mcp_process.poll() is not None:
            print(f"[WEB] MCP server died immediately (exit code: {mcp_process.poll()})")
            try:
                stderr = mcp_process.stderr.read().decode('utf-8', errors='ignore')
                if stderr:
                    print(f"[WEB] Server stderr: {stderr}")
            except:
                pass
            return False
        
        # Create client using the existing SimpleMCPClient
        print("[WEB] Creating MCP client...")
        mcp_client = SimpleMCPClient.from_process(mcp_process)
        
        # Initialize MCP connection
        print("[WEB] Initializing MCP connection...")
        init_success = run_async(mcp_client.initialize())
        
        if not init_success:
            print("[WEB] Failed to initialize MCP client")
            return False
        
        # Discover available tools
        print("[WEB] Discovering available tools...")
        tools = run_async(mcp_client.list_tools())
        
        if tools:
            print(f"[WEB] Found {len(tools)} tools: {list(tools.keys())}")
        else:
            print("[WEB] No tools discovered")
            
        print("[WEB] MCP server and client ready!")
        return True
        
    except Exception as e:
        print(f"[WEB] Error starting MCP server: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup():
    """Cleanup function to properly shut down MCP server and event loop"""
    global mcp_process, event_loop
    
    print("[WEB] Cleaning up...")
    
    if mcp_process:
        try:
            mcp_process.terminate()
            mcp_process.wait(timeout=5)
            print("[WEB] MCP server terminated")
        except subprocess.TimeoutExpired:
            mcp_process.kill()
            print("[WEB] MCP server killed")
        except Exception as e:
            print(f"[WEB] Error terminating MCP server: {e}")
    
    if event_loop:
        try:
            event_loop.call_soon_threadsafe(event_loop.stop)
            print("[WEB] Event loop stopped")
        except Exception as e:
            print(f"[WEB] Error stopping event loop: {e}")

# Register cleanup function
atexit.register(cleanup)

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    """Handle summarization requests"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
            
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        print(f"[WEB] Processing URL: {url}")
        
        # Call the summarize_website tool using the existing client
        result = run_async(mcp_client.call_tool("summarize_website", {"url": url}, timeout=120))
        
        if result is None:
            return jsonify({"error": "Failed to get response from MCP server"}), 500
        
        if result.startswith("❌"):
            return jsonify({"error": result}), 500
        
        return jsonify({"result": result})
        
    except Exception as e:
        print(f"[WEB] Summarize error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    """Test endpoint"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        result = run_async(mcp_client.call_tool("ping"))
        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/tools', methods=['GET'])
def list_tools():
    """List all available tools"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        tools = run_async(mcp_client.list_tools())
        return jsonify({"tools": tools})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    global mcp_process
    
    status = {
        "web_server": "ok",
        "mcp_server": "ok" if mcp_process and mcp_process.poll() is None else "error",
        "tools_available": len(mcp_client.available_tools) if mcp_client else 0
    }
    return jsonify(status)

@app.route('/search', methods=['POST'])
def search_summaries():
    """Search summaries endpoint"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        data = request.get_json()
        keyword = data.get('keyword', '').strip()
        
        if not keyword:
            return jsonify({"error": "Keyword is required"}), 400
        
        result = run_async(mcp_client.call_tool("search_summaries", {"keyword": keyword}))
        return jsonify({"result": result})
        
    except Exception as e:
        print(f"[WEB] Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/summaries', methods=['GET'])
def get_all_summaries():
    """Get all summaries endpoint"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    try:
        result = run_async(mcp_client.call_tool("view_all_summaries"))
        return jsonify({"result": result})
        
    except Exception as e:
        print(f"[WEB] Get summaries error: {e}")
        return jsonify({"error": str(e)}), 500

# Enhanced HTML Template with more features
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SummarizePro - Web Interface</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 40px; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .header p { opacity: 0.9; font-size: 1.1rem; }
        .card { 
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .tabs {
            display: flex;
            margin-bottom: 20px;
            border-bottom: 2px solid #f0f0f0;
        }
        .tab {
            padding: 12px 24px;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 16px;
            color: #666;
            border-bottom: 2px solid transparent;
            transition: all 0.3s ease;
        }
        .tab.active {
            color: #667eea;
            border-bottom-color: #667eea;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .input-group { display: flex; gap: 10px; margin-bottom: 20px; }
        .url-input, .search-input { 
            flex: 1;
            padding: 12px 15px;
            border: 2px solid #e1e5e9;
            border-radius: 8px;
            font-size: 16px;
        }
        .url-input:focus, .search-input:focus { 
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        .btn { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s ease;
            white-space: nowrap;
        }
        .btn:hover:not(:disabled) { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-secondary {
            background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
        }
        .loading { 
            display: none;
            text-align: center;
            color: #667eea;
            padding: 20px;
        }
        .loading.active { display: block; }
        .spinner { 
            display: inline-block;
            width: 30px;
            height: 30px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 10px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .result { 
            display: none;
            background: #f8f9fa;
            border: 1px solid #e1e5e9;
            border-radius: 8px;
            padding: 20px;
            margin-top: 20px;
            white-space: pre-wrap;
            line-height: 1.6;
            max-height: 400px;
            overflow-y: auto;
        }
        .result.active { display: block; }
        .error { background: #fee; border-color: #e74c3c; color: #c0392b; }
        .status { 
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
            color: #666;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        .status-dot { 
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 5px;
        }
        .status-ok { background: #27ae60; }
        .status-error { background: #e74c3c; }
        .summaries-list {
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid #e1e5e9;
            border-radius: 8px;
            padding: 15px;
            background: #f8f9fa;
        }
        .summary-item {
            padding: 10px;
            border-bottom: 1px solid #eee;
            font-size: 14px;
        }
        .summary-item:last-child {
            border-bottom: none;
        }
        .tools-info {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        .tool-card {
            background: #f8f9fa;
            border: 1px solid #e1e5e9;
            border-radius: 8px;
            padding: 15px;
        }
        .tool-name {
            font-weight: bold;
            color: #667eea;
            margin-bottom: 5px;
        }
        .tool-desc {
            font-size: 14px;
            color: #666;
            margin-bottom: 10px;
        }
        .tool-params {
            font-size: 12px;
            color: #888;
        }
        @media (max-width: 768px) {
            .input-group { flex-direction: column; }
            .status { flex-direction: column; align-items: flex-start; }
            .header h1 { font-size: 2rem; }
            .card { padding: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 SummarizePro</h1>
            <p>Web Interface with MCP Server Integration</p>
        </div>

        <div class="card">
            <div class="status">
                <div>
                    <span class="status-dot status-ok"></span>
                    Web Server: Online
                </div>
                <div id="mcpStatus">
                    <span class="status-dot status-error"></span>
                    MCP Server: Checking...
                </div>
                <div id="toolsStatus">
                    Tools: Loading...
                </div>
            </div>

            <div class="tabs">
                <button class="tab active" onclick="switchTab('summarize')">🔗 Summarize</button>
                <button class="tab" onclick="switchTab('search')">🔍 Search</button>
                <button class="tab" onclick="switchTab('browse')">📋 Browse</button>
                <button class="tab" onclick="switchTab('tools')">⚙️ Tools</button>
            </div>

            <!-- Summarize Tab -->
            <div id="summarize-tab" class="tab-content active">
                <h3 style="margin-bottom: 15px;">🔗 Summarize Web Content</h3>
                
                <div class="input-group">
                    <input 
                        type="url" 
                        id="urlInput" 
                        class="url-input" 
                        placeholder="Enter web URL (supports dynamic content via Playwright)"
                        autocomplete="url"
                    >
                    <button class="btn" id="summarizeBtn" onclick="summarizeUrl()">
                        🚀 Summarize
                    </button>
                </div>

                <div class="loading" id="summarizeLoading">
                    <div class="spinner"></div>
                    <div>Processing content... This may take a moment for dynamic websites.</div>
                </div>

                <div class="result" id="summarizeResult"></div>
            </div>

            <!-- Search Tab -->
            <div id="search-tab" class="tab-content">
                <h3 style="margin-bottom: 15px;">🔍 Search Summaries</h3>
                
                <div class="input-group">
                    <input 
                        type="text" 
                        id="searchInput" 
                        class="search-input" 
                        placeholder="Enter keyword to search in summaries"
                    >
                    <button class="btn btn-secondary" onclick="searchSummaries()">
                        🔍 Search
                    </button>
                </div>

                <div class="loading" id="searchLoading">
                    <div class="spinner"></div>
                    <div>Searching summaries...</div>
                </div>

                <div class="result" id="searchResult"></div>
            </div>

            <!-- Browse Tab -->
            <div id="browse-tab" class="tab-content">
                <h3 style="margin-bottom: 15px;">📋 Browse All Summaries</h3>
                
                <button class="btn btn-secondary" onclick="loadAllSummaries()">
                    📂 Load All Summaries
                </button>

                <div class="loading" id="browseLoading">
                    <div class="spinner"></div>
                    <div>Loading summaries...</div>
                </div>

                <div class="result" id="browseResult"></div>
            </div>

            <!-- Tools Tab -->
            <div id="tools-tab" class="tab-content">
                <h3 style="margin-bottom: 15px;">⚙️ Available MCP Tools</h3>
                
                <button class="btn btn-secondary" onclick="loadTools()">
                    🔄 Refresh Tools
                </button>

                <div class="loading" id="toolsLoading">
                    <div class="spinner"></div>
                    <div>Loading tools...</div>
                </div>

                <div id="toolsInfo" class="tools-info"></div>
            </div>
        </div>
    </div>

    <script>
        // Tab switching
        function switchTab(tabName) {
            // Hide all tab contents
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            // Remove active class from all tabs
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Show selected tab content
            document.getElementById(tabName + '-tab').classList.add('active');
            
            // Add active class to clicked tab
            event.target.classList.add('active');
        }

        // Check server status
        async function checkStatus() {
            try {
                const response = await fetch('/health');
                const status = await response.json();
                const mcpStatus = document.getElementById('mcpStatus');
                const toolsStatus = document.getElementById('toolsStatus');
                
                if (status.mcp_server === 'ok') {
                    mcpStatus.innerHTML = '<span class="status-dot status-ok"></span>MCP Server: Online';
                    toolsStatus.innerHTML = `Tools: ${status.tools_available || 0} available`;
                } else {
                    mcpStatus.innerHTML = '<span class="status-dot status-error"></span>MCP Server: Offline';
                    toolsStatus.innerHTML = 'Tools: Not available';
                }
            } catch (error) {
                console.error('Status check failed:', error);
                const mcpStatus = document.getElementById('mcpStatus');
                mcpStatus.innerHTML = '<span class="status-dot status-error"></span>MCP Server: Error';
            }
        }

        // Summarize URL
        async function summarizeUrl() {
            const urlInput = document.getElementById('urlInput');
            const loading = document.getElementById('summarizeLoading');
            const result = document.getElementById('summarizeResult');
            const btn = document.getElementById('summarizeBtn');
            
            const url = urlInput.value.trim();
            if (!url) {
                alert('Please enter a valid URL');
                return;
            }

            // Show loading
            btn.disabled = true;
            btn.textContent = '⏳ Processing...';
            loading.classList.add('active');
            result.classList.remove('active');

            try {
                const response = await fetch('/summarize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });

                const data = await response.json();
                
                if (response.ok) {
                    result.textContent = data.result;
                    result.className = 'result active';
                } else {
                    result.textContent = data.error || 'An error occurred';
                    result.className = 'result active error';
                }
            } catch (error) {
                result.textContent = 'Network error: ' + error.message;
                result.className = 'result active error';
            } finally {
                btn.disabled = false;
                btn.textContent = '🚀 Summarize';
                loading.classList.remove('active');
            }
        }

        // Search summaries
        async function searchSummaries() {
            const searchInput = document.getElementById('searchInput');
            const loading = document.getElementById('searchLoading');
            const result = document.getElementById('searchResult');
            
            const keyword = searchInput.value.trim();
            if (!keyword) {
                alert('Please enter a search keyword');
                return;
            }

            loading.classList.add('active');
            result.classList.remove('active');

            try {
                const response = await fetch('/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ keyword: keyword })
                });

                const data = await response.json();
                
                if (response.ok) {
                    if (Array.isArray(data.result)) {
                        result.textContent = data.result.join('\\n');
                    } else {
                        result.textContent = data.result;
                    }
                    result.className = 'result active';
                } else {
                    result.textContent = data.error || 'Search failed';
                    result.className = 'result active error';
                }
            } catch (error) {
                result.textContent = 'Network error: ' + error.message;
                result.className = 'result active error';
            } finally {
                loading.classList.remove('active');
            }
        }

        // Load all summaries
        async function loadAllSummaries() {
            const loading = document.getElementById('browseLoading');
            const result = document.getElementById('browseResult');

            loading.classList.add('active');
            result.classList.remove('active');

            try {
                const response = await fetch('/summaries');
                const data = await response.json();
                
                if (response.ok) {
                    if (Array.isArray(data.result)) {
                        result.textContent = data.result.join('\\n');
                    } else {
                        result.textContent = data.result;
                    }
                    result.className = 'result active';
                } else {
                    result.textContent = data.error || 'Failed to load summaries';
                    result.className = 'result active error';
                }
            } catch (error) {
                result.textContent = 'Network error: ' + error.message;
                result.className = 'result active error';
            } finally {
                loading.classList.remove('active');
            }
        }

        // Load tools information
        async function loadTools() {
            const loading = document.getElementById('toolsLoading');
            const toolsInfo = document.getElementById('toolsInfo');

            loading.classList.add('active');
            toolsInfo.innerHTML = '';

            try {
                const response = await fetch('/tools');
                const data = await response.json();
                
                if (response.ok && data.tools) {
                    const tools = data.tools;
                    let html = '';
                    
                    for (const [name, tool] of Object.entries(tools)) {
                        const description = tool.description || 'No description available';
                        const schema = tool.inputSchema || {};
                        const properties = schema.properties || {};
                        const required = schema.required || [];
                        
                        html += `
                            <div class="tool-card">
                                <div class="tool-name">🔧 ${name}</div>
                                <div class="tool-desc">${description}</div>
                        `;
                        
                        if (Object.keys(properties).length > 0) {
                            html += '<div class="tool-params"><strong>Parameters:</strong><br>';
                            for (const [paramName, paramInfo] of Object.entries(properties)) {
                                const isRequired = required.includes(paramName);
                                const type = paramInfo.type || 'unknown';
                                const desc = paramInfo.description || 'No description';
                                html += `• ${paramName} (${type})${isRequired ? ' *' : ''}: ${desc}<br>`;
                            }
                            html += '</div>';
                        } else {
                            html += '<div class="tool-params">No parameters required</div>';
                        }
                        
                        html += '</div>';
                    }
                    
                    toolsInfo.innerHTML = html;
                } else {
                    toolsInfo.innerHTML = '<div class="tool-card">❌ Failed to load tools</div>';
                }
            } catch (error) {
                toolsInfo.innerHTML = `<div class="tool-card">❌ Network error: ${error.message}</div>`;
            } finally {
                loading.classList.remove('active');
            }
        }

        // Add enter key support
        document.getElementById('urlInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') summarizeUrl();
        });

        document.getElementById('searchInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') searchSummaries();
        });

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', function() {
            checkStatus();
            loadTools();
            setInterval(checkStatus, 10000); // Check status every 10 seconds
        });
    </script>
</body>
</html>
'''

def create_template_file():
    """Create the HTML template file"""
    os.makedirs('templates', exist_ok=True)
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)

def signal_handler(signum, frame):
    """Handle interrupt signals"""
    print(f"\n[WEB] Received signal {signum}")
    cleanup()
    sys.exit(0)

if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("="*60)
    print("🚀 SUMMARIZEPRO - WEB INTERFACE")
    print("="*60)
    print("Using SimpleMCPClient from summariser_client.py")
    print("="*60)
    
    # Create template file
    create_template_file()
    print("✅ Created HTML template")
    
    # Start MCP server
    if not start_mcp_server():
        print("❌ Failed to start MCP server")
        sys.exit(1)
    
    print("✅ MCP server and client ready")
    print("🌐 Starting web server...")
    print("📍 Open: http://localhost:5000")
    print("="*60)
    
    try:
        app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        cleanup()