import asyncio
import subprocess
import time
from typing import Optional
from playwright.async_api import async_playwright


# ✅ Custom fallback Client class (same as latest fastmcp)
class Client:
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout

    @classmethod
    def from_stdio(cls, stdin, stdout):
        return cls(stdin=stdin, stdout=stdout)

    async def process_query(self, query: str, *, timeout: Optional[float] = 30.0) -> str:
        # Write the query to the server's stdin
        self.stdin.write(query + "\n")
        self.stdin.flush()

        # Read the response from server's stdout
        response = await asyncio.wait_for(asyncio.to_thread(self.stdout.readline), timeout)
        return response.strip()


async def main():
    server_file = "summariser_server.py"  # Update this to your actual server file name

    print("🚀 Starting MCP server subprocess...")
    server_process = subprocess.Popen(
        ["uv", "run", server_file],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    # Optional: give time for MCP server to start
    time.sleep(1)

    client = Client.from_stdio(stdin=server_process.stdin, stdout=server_process.stdout)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = input("🔗 Enter a URL to summarize: ").strip()

        try:
            await page.goto(url)
            print(f"[✅] Opened: {url}")
        except Exception as e:
            print(f"❌ Failed to open URL: {e}")
            await browser.close()
            server_process.terminate()
            return

        try:
            response = await client.process_query(f'summarize_website("{url}")')
            print("\n📄 Summary:\n")
            print(response)
        except Exception as e:
            print(f"❌ Error calling MCP tool: {e}")

        await browser.close()

    print("🛑 Stopping MCP server...")
    server_process.terminate()


if __name__ == "__main__":
    asyncio.run(main())
