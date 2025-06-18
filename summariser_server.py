import os
import json
from datetime import datetime
from typing import List
from newspaper import Article
from mcp.server.fastmcp import FastMCP

# === Constants ===
SUMMARIES_DIR = "summaries"
SUMMARY_LOG = os.path.join(SUMMARIES_DIR, "summaries.json")

# === Init ===
os.makedirs(SUMMARIES_DIR, exist_ok=True)
mcp = FastMCP("web_summarizer")

# === Helper ===
def extract_article(url: str):
    article = Article(url)
    article.download()
    article.parse()
    return article.title, article.text

def dummy_summary(text: str):
    # Use an LLM here if you want (like OpenAI)
    return text[:500] + "..." if len(text) > 500 else text

# === MCP TOOLS ===
@mcp.tool()
def summarize_website(url: str) -> str:
    """
    Extracts and summarizes article content from a given URL.
    """
    try:
        title, content = extract_article(url)
        summary = dummy_summary(content)
        return f"**Title:** {title}\n\n**Summary:**\n{summary}"
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
        with open(SUMMARY_LOG, "r") as f:
            logs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logs = []

    logs.append(log_entry)
    with open(SUMMARY_LOG, "w") as f:
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

# === Run Server ===
if __name__ == "__main__":
    mcp.run(transport="stdio")
