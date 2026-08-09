"""Shared guide file loader for Director planners and guidance modules.

All model-specific, NSFW, and formatting guidance lives in .md files under
`services/llm_guides/director/`. This module provides a single loader function
used by all planners and guidance modules.
"""

import os
from functools import lru_cache

_GUIDES_DIR = os.path.join(os.path.dirname(__file__), "..", "llm_guides", "director")


@lru_cache(maxsize=64)
def load_guide(filename: str) -> str:
    """Load a guide .md file from the director guides directory.

    Returns the file content as a string, or empty string if not found.
    Files are intentionally cached for the life of the Maestro Python process.
    Guide updates take effect after Maestro restarts. Production code has no
    live-reload/cache-clear path; tests may use ``load_guide.cache_clear()``.
    """
    filepath = os.path.join(_GUIDES_DIR, filename)
    if not os.path.isfile(filepath):
        print(f"[GuideLoader] Guide not found: {filename}")
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[GuideLoader] Failed to load {filename}: {e}")
        return ""
