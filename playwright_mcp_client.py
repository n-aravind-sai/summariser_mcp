import asyncio
import subprocess
import sys
import time
from typing import Optional
from playwright.async_api import async_playwright

# ✅ Custom Client using stdio
class Client:
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout

    @classmethod
    def from_stdio(cls, stdin, stdout):
        return cls(stdin=stdin, stdout=stdout)

    async def process_query(self, query: str, *, timeout: Optional[float] = 30.0) -> str:
        try:
            print(f"[CLIENT] Sending query: {query}")
            self.stdin.write(query + "\n")
            self.stdin.flush()

            print("[CLIENT] Awaiting response from MCP server...")
            response = await asyncio.wait_for(asyncio.to_thread(self.stdout.readline), timeout)
            print("[CLIENT] Received response ✅")
            return response.strip()
        except Exception as e:
            raise RuntimeError(f"Error in process_query(): {e}")


async def main():
    server_file = "summariser_server.py"

    print("🚀 Starting MCP server subprocess...")
    server_process = subprocess.Popen(
        [sys.executable, server_file],  # ✅ NOT 'uv run'
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    time.sleep(1)  # Let server warm up

    client = Client.from_stdio(stdin=server_process.stdin, stdout=server_process.stdout)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = input("🔗 Enter a URL to summarize: ").strip()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print(f"[✅] Opened: {url}")
            await page.screenshot(path="debug.png", full_page=True)
            print("🖼️ Screenshot saved as debug.png")
        except Exception as e:
            print(f"❌ Failed to open URL: {e}")
            await browser.close()
            server_process.terminate()
            return

        try:
            query = f'summarize_website("{url}")'
            response = await client.process_query(query)
            print("\n📄 Summary:\n")
            print(response)
        except Exception as e:
            print(f"❌ Error calling MCP tool: {e}")

        await browser.close()

    print("🛑 Stopping MCP server...")
    server_process.terminate()


if __name__ == "__main__":
    asyncio.run(main())
