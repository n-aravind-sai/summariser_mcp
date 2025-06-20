import os
import json
import sys
from datetime import datetime
from typing import List
from newspaper import Article
from mcp.server.fastmcp import FastMCP
import trafilatura
import re
import asyncio
from playwright.async_api import async_playwright

# === Logging Function ===
def log(msg):
    # Log to stderr so it doesn't interfere with stdio communication
    print(f"[SERVER] {msg}", file=sys.stderr, flush=True)

log("Summariser MCP server is starting...")

# === Constants ===
SUMMARIES_DIR = "summaries"
SUMMARY_LOG = os.path.join(SUMMARIES_DIR, "summaries.json")

# === Init ===
os.makedirs(SUMMARIES_DIR, exist_ok=True)
mcp = FastMCP("web_summarizer")

# === Helper Functions ===

async def extract_with_playwright(url: str):
    """Extract content using playwright for dynamic websites"""
    log(f"Attempting playwright extraction for: {url}")
    
    try:
        async with async_playwright() as p:
            # Launch browser (headless by default)
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            )
            
            try:
                # Create new page
                page = await browser.new_page()
                
                # Set a realistic user agent to avoid bot detection
                await page.set_user_agent(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                )
                
                # Navigate to the page with timeout
                log(f"Navigating to: {url}")
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                
                # Wait a bit for dynamic content to load
                await page.wait_for_timeout(3000)
                
                # Try to get the title
                title = await page.title()
                if not title:
                    title = "Untitled"
                
                # Extract main content using multiple selectors
                content_selectors = [
                    'article',
                    '[role="main"]',
                    'main',
                    '.content',
                    '.post-content',
                    '.entry-content',
                    '.article-content',
                    '.story-body',
                    '.post-body',
                    '#content',
                    '.main-content'
                ]
                
                content = ""
                for selector in content_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            text = await element.inner_text()
                            if text and len(text.strip()) > 100:  # Only use if substantial content
                                content = text.strip()
                                log(f"Found content using selector: {selector}")
                                break
                    except Exception as e:
                        log(f"Selector {selector} failed: {e}")
                        continue
                
                # Fallback: get all text from body if no specific content found
                if not content:
                    log("Using fallback: extracting all body text")
                    body_element = await page.query_selector('body')
                    if body_element:
                        content = await body_element.inner_text()
                
                # Clean up content
                if content:
                    # Remove excessive whitespace
                    content = re.sub(r'\n\s*\n', '\n\n', content)
                    content = re.sub(r' +', ' ', content)
                    content = content.strip()
                
                if content and len(content) > 50:
                    log(f"Playwright extraction successful: {len(content)} characters")
                    return title, content
                else:
                    log("Playwright extraction failed: no substantial content found")
                    return None, None
                    
            finally:
                await browser.close()
                
    except Exception as e:
        log(f"Playwright extraction failed: {e}")
        return None, None

def extract_article(url: str):
    log(f"Extracting article from: {url}")
    
    # Try newspaper first (fastest)
    try:
        article = Article(url)
        article.download()
        article.parse()
        if article.text and len(article.text.strip()) > 100:
            log("Successfully extracted with newspaper")
            return article.title or "Untitled", article.text
    except Exception as e:
        log(f"Newspaper failed: {e}")

    # Try trafilatura as second option
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            content = trafilatura.extract(downloaded)
            meta = trafilatura.extract_metadata(downloaded)
            title = getattr(meta, "title", "Untitled") if meta else "Untitled"
            if content and len(content.strip()) > 100:
                log("Successfully extracted with trafilatura")
                return title, content
    except Exception as e:
        log(f"Trafilatura failed: {e}")

    # Try playwright as final fallback (for dynamic content)
    try:
        log("Trying playwright for dynamic content...")
        
        # Since we're in a sync function, we need to run the async playwright function
        if hasattr(asyncio, 'run'):
            # Python 3.7+
            title, content = asyncio.run(extract_with_playwright(url))
        else:
            # Fallback for older Python versions
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                title, content = loop.run_until_complete(extract_with_playwright(url))
            finally:
                loop.close()
        
        if title and content:
            return title, content
            
    except Exception as e:
        log(f"Playwright extraction failed: {e}")

    raise ValueError("Unable to extract article content from URL with any method")

def dummy_summary(text: str):
    """Create a simple summary by taking first few sentences"""
    if not text or not text.strip():
        return "No content available for summary."
    
    # Split into sentences more reliably
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    summary = ""
    word_count = 0
    max_words = 150  # Limit summary length
    
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if word_count + sentence_words > max_words and summary:
            break
        summary += sentence + " "
        word_count += sentence_words
    
    result = summary.strip()
    if len(result) > 500:
        result = result[:500] + "..."
    
    return result if result else text[:200] + "..."

def save_summary_helper(title: str, content: str, tags: List[str]) -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    # Make filename Windows-safe
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:50]  # Limit length too
    
    log_entry = {
        "title": title,
        "tags": tags,
        "timestamp": timestamp,
        "files": []
    }

    for tag in tags:
        tag_dir = os.path.join(SUMMARIES_DIR, tag)
        os.makedirs(tag_dir, exist_ok=True)
        file_name = f"{safe_title}_{timestamp}.txt"
        file_path = os.path.join(tag_dir, file_name)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"Title: {title}\nTags: {', '.join(tags)}\nTimestamp: {timestamp}\n\n{content}")
            log_entry["files"].append(file_path)
            log(f"Saved summary to: {file_path}")
        except Exception as e:
            log(f"Error saving file {file_path}: {e}")

    # Update summary log
    try:
        try:
            with open(SUMMARY_LOG, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logs = []

        logs.append(log_entry)
        with open(SUMMARY_LOG, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
        log("Updated summary log")
    except Exception as e:
        log(f"Error updating summary log: {e}")

    return f"✅ Summary saved in: {', '.join(log_entry['files'])}"

# === MCP TOOLS ===

@mcp.tool()
def summarize_website(url: str) -> str:
    """Summarize content from a website URL (supports dynamic content)"""
    log(f"summarize_website() called with URL: {url}")
    
    try:
        # Validate URL
        if not url or not url.strip():
            return "❌ Error: Empty URL provided"
        
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            log(f"Added https:// to URL: {url}")
        
        # Extract article content (now with playwright support)
        title, content = extract_article(url)
        
        if not content or not content.strip():
            return "❌ Error: No content could be extracted from the URL"

        log(f"Extracted article: '{title[:50]}...' ({len(content)} chars)")
        
        # Generate summary
        summary = dummy_summary(content)
        log(f"Generated summary ({len(summary)} chars)")

        # Generate tag from URL
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            netloc = parsed.netloc.replace("www.", "") if parsed.netloc else "unknown"
            auto_tag = netloc.split('.')[0] if '.' in netloc else "web"
        except:
            auto_tag = "web"

        # Save summary
        try:
            save_msg = save_summary_helper(title=title, content=summary, tags=[auto_tag])
        except Exception as e:
            log(f"Error saving summary: {e}")
            save_msg = "⚠️ Summary generated but not saved due to error"

        # Format result
        result = f"**Title:** {title}\n\n**Summary:**\n{summary}\n\n{save_msg}"
        log("Summary completed successfully")
        return result
        
    except Exception as e:
        error_msg = f"❌ Error processing URL: {str(e)}"
        log(f"ERROR in summarize_website: {e}")
        return error_msg

@mcp.tool()
def save_summary(title: str, content: str, tags: List[str]) -> str:
    """Save a summary with given title, content and tags"""
    log(f"save_summary() called: {title[:30]}...")
    try:
        return save_summary_helper(title, content, tags)
    except Exception as e:
        log(f"Error in save_summary: {e}")
        return f"❌ Error saving summary: {str(e)}"

@mcp.tool()
def get_summary_by_tag(tag: str) -> List[str]:
    """Get all summaries for a specific tag"""
    log(f"get_summary_by_tag() called: {tag}")
    try:
        tag_path = os.path.join(SUMMARIES_DIR, tag)
        if not os.path.isdir(tag_path):
            return [f"❌ No tag named '{tag}' found."]
        files = sorted(os.listdir(tag_path))
        return files if files else [f"❌ No summaries found for tag '{tag}'."]
    except Exception as e:
        log(f"Error in get_summary_by_tag: {e}")
        return [f"❌ Error accessing tag '{tag}': {str(e)}"]

@mcp.tool()
def search_summaries(keyword: str) -> List[str]:
    """Search summaries by keyword"""
    log(f"search_summaries() called: {keyword}")
    try:
        matches = []
        for root, _, files in os.walk(SUMMARIES_DIR):
            for file in files:
                if file.endswith(".txt"):
                    full_path = os.path.join(root, file)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            if keyword.lower() in file.lower() or keyword.lower() in content.lower():
                                matches.append(full_path)
                    except Exception:
                        continue
        return sorted(matches) if matches else ["❌ No matches found."]
    except Exception as e:
        log(f"Error in search_summaries: {e}")
        return [f"❌ Error searching summaries: {str(e)}"]

@mcp.tool()
def view_all_summaries() -> List[str]:
    """View all available summaries"""
    log("view_all_summaries() called")
    try:
        summaries = []
        for root, _, files in os.walk(SUMMARIES_DIR):
            for file in files:
                if file.endswith(".txt"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            first_line = f.readline().strip()
                            summaries.append(f"{file} - {first_line}")
                    except Exception:
                        summaries.append(f"{file} - [Error reading file]")
        return sorted(summaries) if summaries else ["❌ No summaries found."]
    except Exception as e:
        log(f"Error in view_all_summaries: {e}")
        return [f"❌ Error viewing summaries: {str(e)}"]

@mcp.tool()  
def ping() -> str:
    """Simple ping tool to test MCP connection"""
    log("ping() called")
    return "pong - MCP server is working!"

@mcp.tool()
def test_playwright() -> str:
    """Test playwright installation and basic functionality"""
    log("test_playwright() called")
    try:
        # Test a simple webpage
        if hasattr(asyncio, 'run'):
            title, content = asyncio.run(extract_with_playwright("https://example.com"))
        else:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                title, content = loop.run_until_complete(extract_with_playwright("https://example.com"))
            finally:
                loop.close()
        
        if title and content:
            return f"✅ Playwright is working! Test extraction successful.\nTitle: {title}\nContent length: {len(content)} chars"
        else:
            return "⚠️ Playwright is installed but test extraction failed"
            
    except Exception as e:
        return f"❌ Playwright test failed: {str(e)}"

# === Start MCP Server ===
if __name__ == "__main__":
    try:
        log("Starting FastMCP server with stdio transport...")
        log("Playwright support enabled for dynamic websites")
        # Run the MCP server
        mcp.run(transport="stdio")
        
        log("FastMCP server finished normally")
        
    except KeyboardInterrupt:
        log("Server interrupted by user")
    except Exception as e:
        log(f"MCP server crashed: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        sys.exit(1)