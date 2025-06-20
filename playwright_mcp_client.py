import asyncio
import subprocess
import time
import json
import sys
from typing import Optional, Dict, List, Any
from playwright.async_api import async_playwright


class SimpleMCPClient:
    def __init__(self, process):
        self.process = process
        self.request_id = 0
        self.available_tools = {}

    @classmethod
    def from_process(cls, process):
        return cls(process=process)

    def _create_request(self, method: str, params: Optional[dict] = None):
        """Create a JSON-RPC 2.0 request"""
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params or {}
        }
        return json.dumps(request) + "\n"

    async def list_tools(self) -> Dict[str, Any]:
        """List all available tools from the MCP server"""
        print("[CLIENT] 🔍 Discovering available tools...")
        
        try:
            request = self._create_request("tools/list", {})
            self.process.stdin.write(request.encode('utf-8'))
            self.process.stdin.flush()
            
            response_text = await asyncio.wait_for(
                self._read_complete_response(), 
                timeout=10.0
            )
            
            response = json.loads(response_text.strip())
            if "error" in response:
                print(f"[CLIENT] ❌ Error listing tools: {response['error']}")
                return {}
            
            tools = response.get("result", {}).get("tools", [])
            self.available_tools = {tool["name"]: tool for tool in tools}
            
            print(f"[CLIENT] ✅ Found {len(self.available_tools)} tools")
            return self.available_tools
            
        except Exception as e:
            print(f"[CLIENT] ❌ Error listing tools: {e}")
            return {}

    def display_tools(self):
        """Display all available tools in a nice format"""
        if not self.available_tools:
            print("❌ No tools available")
            return
        
        print("\n" + "="*60)
        print("🛠️  AVAILABLE TOOLS")
        print("="*60)
        
        for i, (name, tool_info) in enumerate(self.available_tools.items(), 1):
            description = tool_info.get("description", "No description available")
            input_schema = tool_info.get("inputSchema", {})
            properties = input_schema.get("properties", {})
            
            print(f"\n{i}. 🔧 {name}")
            print(f"   📝 {description}")
            
            if properties:
                print("   📋 Parameters:")
                for param_name, param_info in properties.items():
                    param_type = param_info.get("type", "unknown")
                    param_desc = param_info.get("description", "No description")
                    required = param_name in input_schema.get("required", [])
                    req_mark = " (required)" if required else " (optional)"
                    print(f"      • {param_name} ({param_type}){req_mark}: {param_desc}")
            else:
                print("   📋 No parameters required")
        
        print("\n" + "="*60)

    async def call_tool(self, tool_name: str, arguments: Optional[dict] = None, timeout: float = 60.0) -> str:
        """Call an MCP tool and return the result"""
        print(f"[CLIENT] 🔧 Calling tool: {tool_name}")
        
        try:
            # Create the request
            request = self._create_request("tools/call", {
                "name": tool_name,
                "arguments": arguments or {}
            })
            
            print(f"[CLIENT] 📤 Sending: {json.dumps({'tool': tool_name, 'args': arguments}, indent=2)}")
            
            # Send request to server
            self.process.stdin.write(request.encode('utf-8'))
            self.process.stdin.flush()
            
            # Read response with timeout
            print(f"[CLIENT] ⏳ Waiting for response (timeout: {timeout}s)...")
            
            response_text = await asyncio.wait_for(
                self._read_complete_response(), 
                timeout=timeout
            )
            
            print(f"[CLIENT] 📥 Got response: {response_text[:100]}...")
            
            # Parse the JSON-RPC response
            try:
                response = json.loads(response_text.strip())
                if "error" in response:
                    error_msg = response["error"]
                    print(f"[CLIENT] ❌ Server error: {error_msg}")
                    return f"❌ Server error: {error_msg}"
                
                result = response.get("result", "")
                if isinstance(result, dict):
                    # If result is a dict, try to extract content
                    content = result.get("content", result.get("text", str(result)))
                    if isinstance(content, list):
                        # Handle content arrays
                        return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
                    return str(content)
                else:
                    return str(result)
                    
            except json.JSONDecodeError as e:
                print(f"[CLIENT] ⚠️ JSON decode error: {e}")
                return response_text.strip()
            
        except asyncio.TimeoutError:
            print(f"[CLIENT] ⏰ Timeout after {timeout} seconds")
            return f"❌ Timeout: Server didn't respond within {timeout} seconds"
            
        except Exception as e:
            print(f"[CLIENT] ❌ Error: {e}")
            
            # Check if server process died
            if self.process.poll() is not None:
                try:
                    stderr = self.process.stderr.read().decode('utf-8', errors='ignore')
                    if stderr:
                        print(f"[CLIENT] 🔍 Server stderr: {stderr}")
                except:
                    pass
                return "❌ Server process died unexpectedly"
            
            return f"❌ Error calling tool: {e}"

    async def _read_complete_response(self):
        """Read a complete JSON response from the server"""
        buffer = ""
        
        # Read until we get a complete JSON object
        while True:
            # Check if server process is still alive
            if self.process.poll() is not None:
                print(f"[CLIENT] ⚠️ Server process ended with code: {self.process.poll()}")
                break
            
            try:
                # Read available data
                chunk = await asyncio.to_thread(self.process.stdout.read, 1024)
                if not chunk:
                    print("[CLIENT] 📭 No more data from server")
                    break
                
                chunk_text = chunk.decode('utf-8', errors='ignore')
                buffer += chunk_text
                
                # Try to find complete JSON objects in buffer
                lines = buffer.split('\n')
                for i, line in enumerate(lines):
                    line = line.strip()
                    if line and self._is_complete_json(line):
                        print(f"[CLIENT] ✅ Found complete JSON response")
                        return line
                
                # Keep the last incomplete line in buffer
                buffer = lines[-1] if lines else ""
                
                # Prevent infinite growth
                if len(buffer) > 10000:
                    print("[CLIENT] ⚠️ Buffer too large, returning what we have")
                    break
                    
            except Exception as e:
                print(f"[CLIENT] 🔍 Read error: {e}")
                break
        
        return buffer.strip()

    def _is_complete_json(self, text):
        """Check if text is a complete JSON object"""
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError:
            return False

    async def initialize(self):
        """Initialize the MCP connection"""
        print("[CLIENT] 🤝 Initializing MCP connection...")
        
        try:
            # Send initialize request
            init_request = self._create_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "clientInfo": {
                    "name": "interactive_mcp_client",
                    "version": "2.0.0"
                }
            })
            
            self.process.stdin.write(init_request.encode('utf-8'))
            self.process.stdin.flush()
            
            # Wait for initialization response
            try:
                response = await asyncio.wait_for(self._read_complete_response(), timeout=10.0)
                print(f"[CLIENT] 📥 Init response: {response[:100]}...")
                
                # Send initialized notification
                initialized_notification = json.dumps({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized"
                }) + "\n"
                
                self.process.stdin.write(initialized_notification.encode('utf-8'))
                self.process.stdin.flush()
                
                print("[CLIENT] ✅ MCP initialization complete")
                return True
                
            except asyncio.TimeoutError:
                print("[CLIENT] ⏰ Initialization timeout")
                return False
                
        except Exception as e:
            print(f"[CLIENT] ❌ Initialization error: {e}")
            return False

    def get_tool_parameters(self, tool_name: str) -> Dict[str, Any]:
        """Get parameter information for a specific tool"""
        if tool_name not in self.available_tools:
            return {}
        
        tool_info = self.available_tools[tool_name]
        input_schema = tool_info.get("inputSchema", {})
        return input_schema.get("properties", {})

    def collect_parameters(self, tool_name: str) -> Dict[str, Any]:
        """Interactively collect parameters for a tool"""
        if tool_name not in self.available_tools:
            print(f"❌ Tool '{tool_name}' not found")
            return {}
        
        tool_info = self.available_tools[tool_name]
        input_schema = tool_info.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        
        if not properties:
            return {}
        
        print(f"\n📋 Collecting parameters for '{tool_name}':")
        parameters = {}
        
        for param_name, param_info in properties.items():
            param_type = param_info.get("type", "string")
            param_desc = param_info.get("description", "No description")
            is_required = param_name in required
            
            while True:
                prompt = f"  {param_name} ({param_type}){'*' if is_required else ''}: {param_desc}\n  > "
                value = input(prompt).strip()
                
                if not value and is_required:
                    print("    ❌ This parameter is required!")
                    continue
                
                if not value:
                    break
                
                # Type conversion
                try:
                    if param_type == "integer":
                        parameters[param_name] = int(value)
                    elif param_type == "number":
                        parameters[param_name] = float(value)
                    elif param_type == "boolean":
                        parameters[param_name] = value.lower() in ('true', '1', 'yes', 'y')
                    elif param_type == "array":
                        # Simple array handling - split by comma
                        parameters[param_name] = [item.strip() for item in value.split(',')]
                    else:
                        parameters[param_name] = value
                    break
                except ValueError:
                    print(f"    ❌ Invalid {param_type} value!")
                    continue
        
        return parameters


async def interactive_mode(client):
    """Interactive mode for calling tools"""
    print("\n🎮 Interactive Mode - Type 'help' for commands")
    
    while True:
        try:
            command = input("\n🔧 > ").strip()
            
            if not command:
                continue
            
            if command.lower() in ['exit', 'quit', 'q']:
                print("👋 Goodbye!")
                break
            
            if command.lower() in ['help', 'h']:
                print("\n📖 Available commands:")
                print("  • list - Show all available tools")
                print("  • call <tool_name> - Call a specific tool")
                print("  • <tool_name> - Quick call a tool (same as 'call <tool_name>')")
                print("  • help - Show this help")
                print("  • quit/exit - Exit the program")
                continue
            
            if command.lower() == 'list':
                client.display_tools()
                continue
            
            # Handle 'call <tool_name>' or just '<tool_name>'
            if command.startswith('call '):
                tool_name = command[5:].strip()
            else:
                tool_name = command
            
            if tool_name in client.available_tools:
                print(f"\n🔧 Preparing to call '{tool_name}'...")
                
                # Collect parameters
                parameters = client.collect_parameters(tool_name)
                
                print(f"\n🚀 Calling '{tool_name}' with parameters: {parameters}")
                result = await client.call_tool(tool_name, parameters)
                
                print("\n" + "="*60)
                print("📄 RESULT")
                print("="*60)
                print(result)
                print("="*60)
            else:
                print(f"❌ Tool '{tool_name}' not found. Type 'list' to see available tools.")
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")


async def main():
    server_file = "summariser_server.py"

    print("🚀 Starting MCP server...")
    
    # Start the server process
    try:
        server_process = subprocess.Popen(
            [sys.executable, server_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            universal_newlines=False
        )
        print(f"✅ Server started (PID: {server_process.pid})")
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        return

    # Give server time to start
    print("⏳ Waiting for server startup...")
    await asyncio.sleep(2)
    
    # Check if server is still running
    if server_process.poll() is not None:
        print(f"❌ Server died immediately (exit code: {server_process.poll()})")
        try:
            stderr = server_process.stderr.read().decode('utf-8', errors='ignore')
            if stderr:
                print(f"🔍 Server error: {stderr}")
        except:
            pass
        return

    # Create client and initialize
    client = SimpleMCPClient.from_process(server_process)
    
    if not await client.initialize():
        print("❌ Failed to initialize MCP connection")
        server_process.terminate()
        return
    
    # Discover and list available tools
    await client.list_tools()
    client.display_tools()
    
    # Test connection
    print("\n🏓 Testing connection...")
    if "ping" in client.available_tools:
        result = await client.call_tool("ping")
        print(f"🏓 Ping result: {result}")
    
    # Enter interactive mode
    await interactive_mode(client)

    # Cleanup
    print("\n🛑 Shutting down...")
    try:
        server_process.terminate()
        server_process.wait(timeout=5)
        print("✅ Server terminated gracefully")
    except subprocess.TimeoutExpired:
        print("⚠️ Force killing server...")
        server_process.kill()
    except Exception as e:
        print(f"⚠️ Error during shutdown: {e}")

    print("✅ Done!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()