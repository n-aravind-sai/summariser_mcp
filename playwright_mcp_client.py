import asyncio
import subprocess
import time
import json
import sys
from typing import Optional
from playwright.async_api import async_playwright


class SimpleMCPClient:
    def __init__(self, process):
        self.process = process
        self.request_id = 0

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

    async def call_tool(self, tool_name: str, arguments: Optional[dict] = None, timeout: float = 60.0) -> str:
        """Call an MCP tool and return the result"""
        print(f"[CLIENT] 🔧 Calling tool: {tool_name}")
        
        try:
            # Create the request
            request = self._create_request("tools/call", {
                "name": tool_name,
                "arguments": arguments or {}
            })
            
            print(f"[CLIENT] 📤 Sending: {request.strip()}")
            
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
                    "name": "playwright_client",
                    "version": "1.0.0"
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

    async def test_connection(self):
        """Test the MCP connection with a simple ping"""
        print("[CLIENT] 🏓 Testing connection...")
        try:
            result = await self.call_tool("ping", {})
            print(f"[CLIENT] 🏓 Ping result: {result}")
            return "pong" in result.lower()
        except Exception as e:
            print(f"[CLIENT] 🏓 Ping failed: {e}")
            return False


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
    
    # Test connection
    if not await client.test_connection():
        print("⚠️ Connection test failed, but continuing...")

    # Start Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Get URL from user
        url = input("\n🔗 Enter a URL to summarize: ").strip()
        if not url:
            print("❌ No URL provided")
            await browser.close()
            server_process.terminate()
            return
            
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            print(f"🔗 Using URL: {url}")

        try:
            print(f"🌐 Loading page: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            print("✅ Page loaded successfully")
            
            # Take screenshot for debugging
            await page.screenshot(path="debug.png", full_page=True)
            print("📸 Screenshot saved as debug.png")
            
        except Exception as e:
            print(f"⚠️ Failed to load page: {e}")
            print("🔄 Continuing with summarization anyway...")

        try:
            print("\n🔄 Calling summarization tool...")
            
            # Call the MCP tool to summarize
            result = await client.call_tool("summarize_website", {"url": url})
            
            print("\n" + "="*60)
            print("📄 SUMMARY RESULT")
            print("="*60)
            print(result)
            print("="*60)
            
        except Exception as e:
            print(f"❌ Error during summarization: {e}")

        await browser.close()

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