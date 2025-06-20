"""
MCP Server Management Utility
Handles MCP server lifecycle and communication
"""

import subprocess
import asyncio
import sys
import time
import logging
from threading import Thread
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class MCPManager:
    """Manages MCP server process and client communication"""
    
    def __init__(self, server_script: str = "summariser_server.py"):
        self.server_script = server_script
        self.mcp_process: Optional[subprocess.Popen] = None
        self.mcp_client = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.loop_thread: Optional[Thread] = None
        self.available_tools: Dict[str, Any] = {}
        self._startup_timeout = 10
        self._operation_timeout = 60
    
    def _start_event_loop(self):
        """Start the asyncio event loop in a separate thread"""
        try:
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            logger.debug("Event loop started")
            self.event_loop.run_forever()
        except Exception as e:
            logger.error(f"Error in event loop: {e}")
    
    def _run_async(self, coro, timeout: int = None):
        """Run an async function in the event loop thread"""
        if self.event_loop is None:
            logger.error("Event loop not available")
            return None
        
        timeout = timeout or self._operation_timeout
        
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
            return future.result(timeout=timeout)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            return None
    
    def start(self) -> bool:
        """Start the MCP server process and initialize client"""
        try:
            logger.info("Starting MCP manager...")
            
            # Start event loop thread
            logger.debug("Starting event loop thread...")
            self.loop_thread = Thread(target=self._start_event_loop, daemon=True)
            self.loop_thread.start()
            time.sleep(1)  # Give thread time to start
            
            # Start MCP server process
            logger.info("Starting MCP server process...")
            self.mcp_process = subprocess.Popen(
                [sys.executable, self.server_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                universal_newlines=False
            )
            
            # Give server time to start
            logger.debug("Waiting for MCP server to start...")
            time.sleep(3)
            
            if self.mcp_process.poll() is not None:
                logger.error(f"MCP server died immediately (exit code: {self.mcp_process.poll()})")
                self._log_server_error()
                return False
            
            # Create and initialize client
            if not self._initialize_client():
                return False
            
            # Discover available tools
            if not self._discover_tools():
                logger.warning("Failed to discover tools, but server is running")
            
            logger.info("MCP manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error starting MCP manager: {e}", exc_info=True)
            return False
    
    def _log_server_error(self):
        """Log MCP server error output"""
        if self.mcp_process and self.mcp_process.stderr:
            try:
                stderr = self.mcp_process.stderr.read().decode('utf-8', errors='ignore')
                if stderr:
                    logger.error(f"MCP server stderr: {stderr}")
            except Exception as e:
                logger.error(f"Failed to read server stderr: {e}")
    
    def _initialize_client(self) -> bool:
        """Initialize the MCP client"""
        try:
            # Import the SimpleMCPClient
            from summariser_client import SimpleMCPClient
            
            logger.debug("Creating MCP client...")
            self.mcp_client = SimpleMCPClient.from_process(self.mcp_process)
            
            logger.debug("Initializing MCP connection...")
            init_success = self._run_async(
                self.mcp_client.initialize(),
                timeout=self._startup_timeout
            )
            
            if not init_success:
                logger.error("Failed to initialize MCP client")
                return False
            
            logger.debug("MCP client initialized successfully")
            return True
            
        except ImportError:
            logger.error("Could not import SimpleMCPClient. Make sure summariser_client.py is available.")
            return False
        except Exception as e:
            logger.error(f"Error initializing MCP client: {e}")
            return False
    
    def _discover_tools(self) -> bool:
        """Discover available tools from the MCP server"""
        try:
            logger.debug("Discovering available tools...")
            tools = self._run_async(self.mcp_client.list_tools())
            
            if tools:
                self.available_tools = tools
                logger.info(f"Discovered {len(tools)} tools: {list(tools.keys())}")
                return True
            else:
                logger.warning("No tools discovered")
                return False
                
        except Exception as e:
            logger.error(f"Error discovering tools: {e}")
            return False
    
    def is_healthy(self) -> bool:
        """Check if the MCP server is healthy"""
        return (
            self.mcp_process is not None and
            self.mcp_process.poll() is None and
            self.mcp_client is not None and
            self.event_loop is not None
        )
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None, timeout: int = None) -> Optional[str]:
        """Call a tool on the MCP server"""
        if not self.is_healthy():
            logger.error("MCP server not healthy for tool call")
            return None
        
        if tool_name not in self.available_tools:
            logger.warning(f"Tool '{tool_name}' not available")
            return f"❌ Tool '{tool_name}' not available"
        
        try:
            logger.debug(f"Calling tool: {tool_name} with arguments: {arguments}")
            result = self._run_async(
                self.mcp_client.call_tool(tool_name, arguments or {}),
                timeout=timeout
            )
            
            if result is None:
                logger.error(f"Tool call '{tool_name}' returned None")
                return "❌ Tool call failed"
            
            logger.debug(f"Tool call '{tool_name}' completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}': {e}")
            return f"❌ Error calling tool: {str(e)}"
    
    def list_tools(self) -> Dict[str, Any]:
        """Get the list of available tools"""
        return self.available_tools.copy()
    
    def cleanup(self):
        """Cleanup MCP server and resources"""
        logger.info("Cleaning up MCP manager...")
        
        # Terminate MCP process
        if self.mcp_process:
            try:
                logger.debug("Terminating MCP server process...")
                self.mcp_process.terminate()
                self.mcp_process.wait(timeout=5)
                logger.info("MCP server process terminated")
            except subprocess.TimeoutExpired:
                logger.warning("MCP server process did not terminate, killing...")
                self.mcp_process.kill()
                logger.info("MCP server process killed")
            except Exception as e:
                logger.error(f"Error terminating MCP server process: {e}")
        
        # Stop event loop
        if self.event_loop:
            try:
                logger.debug("Stopping event loop...")
                self.event_loop.call_soon_threadsafe(self.event_loop.stop)
                logger.debug("Event loop stopped")
            except Exception as e:
                logger.error(f"Error stopping event loop: {e}")
        
        # Clean up references
        self.mcp_process = None
        self.mcp_client = None
        self.event_loop = None
        self.available_tools = {}
        
        logger.info("MCP manager cleanup completed")