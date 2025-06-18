import os
import json
from datetime import datetime
from typing import List
from newspaper import Article
from mcp.server.fastmcp import FastMCP

# Optional fallback
import trafilatura

# === Constants ===
SUMMARIES_DIR = "summaries"
SUMMARY_LOG = os.path.join(SUMMARIES_DIR, "summaries.json")

# === Init ===
os.makedirs(SUMMARIES_DIR, exist_ok=True)
mcp = FastMCP("web_summarizer")

# === Helper Functions ===

def extract_article(url: str):
    """
    Tries to extract article using Newspaper. Falls back to trafilatura if needed.
    """
    try:
        article = Article(url)
        article.download()
        article.parse()
        if article.text.strip():
            return article.title or "Untitled", article.text
    except Exception:
        pass

    # Fallback to trafilatura
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        content = trafilatura.extract(downloaded)
        meta = trafilatura.extract_metadata(downloaded)
        title = getattr(meta, "title", "Untitled") if meta else "Untitled"
        return title, content or ""
    
    raise ValueError("Unable to extract article content")

def dummy_summary(text: str):
    """
    Returns first few complete sentences up to ~500 chars.
    """
    import re
    sentences = re.split(r'(?<=[.!?]) +', text)
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence) > 500:
            break
        summary += sentence + " "
    return summary.strip() + "..." if summary else text[:500] + "..."

# === MCP TOOLS ===

@mcp.tool()
def summarize_website(url: str) -> str:
    """
    Extracts and summarizes article content from a given URL, then automatically saves it.
    """
    try:
        title, content = extract_article(url)
        summary = dummy_summary(content)

        # Auto-tag based on domain or use default
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.replace("www.", "")
        auto_tag = netloc.split('.')[0] if '.' in netloc else "auto"

        # Save summary automatically
        save_msg = save_summary(title=title, content=summary, tags=[auto_tag])

        return f"**Title:** {title}\n\n**Summary:**\n{summary}\n\n{save_msg}"
    except Exception as e:
        return f"❌ Error extracting from URL: {str(e)}"


@mcp.tool()
def save_summary(title: str, content: str, tags: List[str]) -> str:
    """
    Saves a summary to file under given tags and logs it.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_title = title.replace(" ", "_").replace("/", "_")
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

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"Title: {title}\nTags: {', '.join(tags)}\n\n{content}")
        log_entry["files"].append(file_path)

    # Update log
    try:
        with open(SUMMARY_LOG, "r", encoding="utf-8") as f:
            logs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logs = []

    logs.append(log_entry)
    with open(SUMMARY_LOG, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)

    return f"✅ Summary saved in: {', '.join(log_entry['files'])}"

@mcp.tool()
def get_summary_by_tag(tag: str) -> List[str]:
    """
    List all summaries saved under a given tag.
    """
    tag_path = os.path.join(SUMMARIES_DIR, tag)
    if not os.path.isdir(tag_path):
        return [f"❌ No tag named '{tag}' found."]
    return os.listdir(tag_path)

@mcp.tool()
def search_summaries(keyword: str) -> List[str]:
    """
    Searches for summaries containing a keyword in title or content.
    """
    matches = []
    for root, _, files in os.walk(SUMMARIES_DIR):
        for file in files:
            if file.endswith(".txt"):
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        if keyword.lower() in content.lower():
                            matches.append(full_path)
                except Exception:
                    continue
    return matches if matches else ["❌ No matches found."]

@mcp.tool()
def view_all_summaries() -> List[str]:
    """
    View all saved summary file names across all tags.
    """
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
    return summaries if summaries else ["📭 No summaries found."]

# === Run Server ===
if __name__ == "__main__":
    mcp.run(transport="stdio")
