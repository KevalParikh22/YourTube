"""
Classification engine for grouping YouTube videos into thematic categories.
Combines fast heuristic pattern matching on titles/channels with custom taxonomy support.
"""
import os
import re
import json
from typing import List, Dict, Any, Optional

FALLBACK_CATEGORY = "General / Other"
# User-edited taxonomy (written by the dashboard); DEFAULT_TAXONOMY is used when absent
TAXONOMY_PATH = os.environ.get("YTKB_TAXONOMY") or os.path.join(os.path.dirname(__file__), "taxonomy.json")

# Default category taxonomy and signature keyword rules
DEFAULT_TAXONOMY: Dict[str, List[str]] = {
    "AI & Machine Learning": [
        "ai", "llm", "gpt", "rag", "langchain", "deep learning", "machine learning",
        "neural network", "transformer", "agent", "agents", "openai", "claude",
        "gemini", "pytorch", "tensorflow", "prompt", "diffusion", "vision", "nlp"
    ],
    "System Design & Architecture": [
        "system design", "architecture", "microservices", "distributed", "scalability",
        "database", "sql", "nosql", "postgres", "redis", "kafka", "caching", "sharding",
        "concurrency", "load balancer", "high availability", "design patterns"
    ],
    "Software Engineering & Coding": [
        "python", "javascript", "typescript", "golang", "rust", "react", "next.js",
        "frontend", "backend", "full stack", "api", "rest", "graphql", "algorithms",
        "data structures", "leetcode", "clean code", "refactoring", "c++", "java"
    ],
    "DevOps, Cloud & Infrastructure": [
        "docker", "kubernetes", "k8s", "aws", "azure", "gcp", "ci/cd", "terraform",
        "devops", "linux", "bash", "git", "github", "monitoring", "prometheus", "grafana"
    ],
    "Productivity & Career": [
        "productivity", "resume", "interview", "career", "salary", "promotion",
        "soft skills", "study", "notion", "obsidian", "time management", "habit"
    ],
    "Business, Tech Trends & Podcasts": [
        "podcast", "interview with", "startup", "founder", "tech news", "future of",
        "crypto", "finance", "investing", "market", "economy"
    ]
}

def taxonomy_path(library: Optional[int] = None, path: str = TAXONOMY_PATH) -> str:
    """Library 1 (the original one) uses taxonomy.json; library N uses taxonomy-N.json next to it."""
    if not library or int(library) == 1:
        return path
    root, ext = os.path.splitext(path)
    return f"{root}-{int(library)}{ext}"

def load_taxonomy(path: str = TAXONOMY_PATH, library: Optional[int] = None) -> Dict[str, List[str]]:
    """
    A library's categories and keywords. Library 1 falls back to the built-in default taxonomy;
    other libraries start empty (everything is General / Other until categories are added).
    """
    path = taxonomy_path(library, path)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): [str(kw) for kw in v] for k, v in data.items()}
    if library and int(library) != 1:
        return {}
    return {k: list(v) for k, v in DEFAULT_TAXONOMY.items()}

def save_taxonomy(taxonomy: Dict[str, List[str]], path: str = TAXONOMY_PATH, library: Optional[int] = None) -> None:
    with open(taxonomy_path(library, path), "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, indent=2, ensure_ascii=False)

def classify_video(
    title: str,
    channel: str = "",
    taxonomy: Optional[Dict[str, List[str]]] = None
) -> str:
    """
    Classifies a single video based on title and channel keywords.
    Returns the best matching category name or 'General / Other'.
    """
    tax = load_taxonomy() if taxonomy is None else taxonomy
    text = f"{title.lower()} {channel.lower()}"

    # Tokenize words for accurate matching
    tokens = set(re.findall(r'\b[a-z0-9\.\+#\-]+\b', text))

    best_category = FALLBACK_CATEGORY
    max_score = 0
    
    for category, keywords in tax.items():
        score = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if " " in kw_lower:
                if kw_lower in text:
                    score += 2  # Multi-word phrases receive higher weight
            else:
                if kw_lower in tokens:
                    score += 1
                    
        if score > max_score:
            max_score = score
            best_category = category
            
    return best_category

def classify_playlist_videos(
    videos: List[Dict[str, Any]],
    custom_taxonomy: Optional[Dict[str, List[str]]] = None
) -> List[Dict[str, Any]]:
    """
    Classifies a list of videos and adds or updates the 'category' key on each item.
    """
    taxonomy = load_taxonomy() if custom_taxonomy is None else custom_taxonomy
    for v in videos:
        # Preserve existing manual assignment if already explicitly set
        if not v.get("category") or v.get("category") == "Uncategorized":
            v["category"] = classify_video(
                title=v.get("title", ""),
                channel=v.get("channel", ""),
                taxonomy=taxonomy
            )
    return videos
