from flask import Flask, render_template, request, jsonify
import subprocess
import json
import asyncio
import sys
import os
from threading import Thread
import time
from typing import Optional, Dict, Any

app = Flask(__name__)

# Global variables to manage MCP client
mcp_process: Optional[subprocess.Popen] = None
mcp_client: Optional['WebMCPClient'] = None

class WebMCPClient:
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.request_id = 0

    def _create_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Create a JSON-RPC 2.0 request"""
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,  
            "method": method,
            "params": params or {}
        }
        return json.dumps(request) + "\n"

    def call_tool_sync(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
        """Synchronous wrapper for calling MCP tools"""
        try:
            # Create the request
            request = self._create_request("tools/call", {
                "name": tool_name,
                "arguments": arguments or {}
            })
            
            print(f"[WEB] Calling tool: {tool_name} with args: {arguments}")
            
            # Check if process is still alive before writing
            if self.process.poll() is not None:
                return {"error": "Server process died"}
            
            # Send request
            if self.process.stdin:
                self.process.stdin.write(request.encode('utf-8'))
                self.process.stdin.flush()
            else:
                return {"error": "Server stdin not available"}
            
            # Read response (simplified for synchronous operation)
            response_text = ""
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
                    return {"error": "Server process died"}
                
                try:
                    # Read with timeout
                    if self.process.stdout:
                        chunk = self.process.stdout.read(1024)
                        if chunk:
                            chunk_text = chunk.decode('utf-8', errors='ignore')
                            response_text += chunk_text
                            
                            # Try to find complete JSON
                            lines = response_text.split('\n')
                            for line in lines:
                                line = line.strip()
                                if line and self._is_complete_json(line):
                                    response = json.loads(line)
                                    if "error" in response:
                                        return {"error": response["error"]}
                                    
                                    result = response.get("result", "")
                                    if isinstance(result, dict):
                                        content = result.get("content", result.get("text", str(result)))
                                        if isinstance(content, list):
                                            return {"result": "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)}
                                        return {"result": str(content)}
                                    return {"result": str(result)}
                    else:
                        return {"error": "Server stdout not available"}
                except Exception as e:
                    print(f"[WEB] Read error: {e}")
                    time.sleep(0.1)
                    continue
            
            return {"error": f"Timeout after {timeout} seconds"}
            
        except Exception as e:
            print(f"[WEB] Tool call error: {e}")
            return {"error": str(e)}

    def _is_complete_json(self, text: str) -> bool:
        """Check if text is complete JSON"""
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError:
            return False

def start_mcp_server() -> bool:
    """Start the MCP server process"""
    global mcp_process, mcp_client
    
    try:
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
        time.sleep(2)
        
        if mcp_process.poll() is not None:
            print(f"[WEB] MCP server died immediately (exit code: {mcp_process.poll()})")
            return False
        
        # Create client
        mcp_client = WebMCPClient(mcp_process)
        
        # Initialize MCP connection
        init_success = initialize_mcp_client()
        if not init_success:
            print("[WEB] Failed to initialize MCP client")
            return False
            
        print("[WEB] MCP server and client ready!")
        return True
        
    except Exception as e:
        print(f"[WEB] Error starting MCP server: {e}")
        return False

def initialize_mcp_client() -> bool:
    """Initialize the MCP client connection"""
    global mcp_process, mcp_client
    
    if not mcp_process or not mcp_client:
        return False
        
    try:
        # Send initialize request
        init_request = mcp_client._create_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {
                "name": "web_client",
                "version": "1.0.0"
            }
        })
        
        if mcp_process.stdin:
            mcp_process.stdin.write(init_request.encode('utf-8'))
            mcp_process.stdin.flush()
        else:
            return False
        
        # Wait a moment for response
        time.sleep(1)
        
        # Send initialized notification
        initialized_notification = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }) + "\n"
        
        if mcp_process.stdin:
            mcp_process.stdin.write(initialized_notification.encode('utf-8'))
            mcp_process.stdin.flush()
        else:
            return False
        
        return True
        
    except Exception as e:
        print(f"[WEB] MCP initialization error: {e}")
        return False

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
        
        # Call the summarize_website tool
        result = mcp_client.call_tool_sync("summarize_website", {"url": url})
        
        if "error" in result:
            return jsonify({"error": result["error"]}), 500
        
        return jsonify({"result": result["result"]})
        
    except Exception as e:
        print(f"[WEB] Summarize error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    """Test endpoint"""
    global mcp_client, mcp_process
    
    if not mcp_client or not mcp_process or mcp_process.poll() is not None:
        return jsonify({"error": "MCP server not available"}), 500
    
    result = mcp_client.call_tool_sync("ping")
    return jsonify(result)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    global mcp_process
    
    status = {
        "web_server": "ok",
        "mcp_server": "ok" if mcp_process and mcp_process.poll() is None else "error"
    }
    return jsonify(status)

# HTML Template (we'll put this in templates folder)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SummarizePro - Prototype</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 40px; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .card { 
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .input-group { display: flex; gap: 10px; margin-bottom: 20px; }
        .url-input { 
            flex: 1;
            padding: 12px 15px;
            border: 2px solid #e1e5e9;
            border-radius: 8px;
            font-size: 16px;
        }
        .url-input:focus { 
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
        }
        .btn:hover:not(:disabled) { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 SummarizePro</h1>
            <p>Prototype - Web Interface + MCP Server</p>
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
            </div>

            <h3 style="margin-bottom: 15px;">🔗 Summarize Web Content</h3>
            
            <div class="input-group">
                <input 
                    type="url" 
                    id="urlInput" 
                    class="url-input" 
                    placeholder="Enter any web URL (e.g., https://example.com/article)"
                    autocomplete="url"
                >
                <button class="btn" id="summarizeBtn" onclick="summarizeUrl()">
                    🚀 Summarize
                </button>
            </div>

            <div class="loading" id="loading">
                <div class="spinner"></div>
                <div>Processing content...</div>
            </div>

            <div class="result" id="result"></div>
        </div>
    </div>

    <script>
        // Check MCP server status
        async function checkStatus() {
            try {
                const response = await fetch('/health');
                const status = await response.json();
                const mcpStatus = document.getElementById('mcpStatus');
                
                if (status.mcp_server === 'ok') {
                    mcpStatus.innerHTML = '<span class="status-dot status-ok"></span>MCP Server: Online';
                } else {
                    mcpStatus.innerHTML = '<span class="status-dot status-error"></span>MCP Server: Offline';
                }
            } catch (error) {
                console.error('Status check failed:', error);
                const mcpStatus = document.getElementById('mcpStatus');
                mcpStatus.innerHTML = '<span class="status-dot status-error"></span>MCP Server: Error';
            }
        }

        async function summarizeUrl() {
            const urlInput = document.getElementById('urlInput');
            const loading = document.getElementById('loading');
            const result = document.getElementById('result');
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
                    headers: {
                        'Content-Type': 'application/json',
                    },
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
                // Hide loading
                btn.disabled = false;
                btn.textContent = '🚀 Summarize';
                loading.classList.remove('active');
            }
        }

        // Add enter key support
        document.getElementById('urlInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                summarizeUrl();
            }
        });

        // Check status on load and periodically
        document.addEventListener('DOMContentLoaded', function() {
            checkStatus();
            setInterval(checkStatus, 10000); // Check every 10 seconds
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

if __name__ == '__main__':
    print("="*60)
    print("🚀 SUMMARIZEPRO PROTOTYPE")
    print("="*60)
    
    # Create template file
    create_template_file()
    print("✅ Created HTML template")
    
    # Start MCP server
    if not start_mcp_server():
        print("❌ Failed to start MCP server")
        sys.exit(1)
    
    print("✅ MCP server ready")
    print("🌐 Starting web server...")
    print("📍 Open: http://localhost:5000")
    print("="*60)
    
    try:
        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        if mcp_process:
            mcp_process.terminate()
            print("✅ MCP server stopped")