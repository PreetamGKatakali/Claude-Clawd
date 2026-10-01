"""Map a prompt to a topic label, locally, with keyword heuristics.

No model call and no network. The prompt text is read in memory, scored, and
discarded: only the topic key and an integer seed are ever returned, and only
those are written to disk.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clawd_common as common  # noqa: E402

# A prompt at least this long with no clear topic reads as a big piece of work.
LONG_PROMPT = 400
# Past this length it is a big task unless a topic matches strongly.
VERY_LONG_PROMPT = 800
STRONG_MATCH = 3


def _score(text, keywords):
    """Count whole-word keyword hits. Multi-word keywords match as phrases."""
    total = 0
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        pattern = r"(?<!\w)" + re.escape(kw) + r"(?!\w)"
        total += len(re.findall(pattern, text))
    return total


def classify(prompt):
    """Return a topic key. Pure function, safe on any input including None."""
    if not isinstance(prompt, str):
        return "default"
    text = prompt.lower()
    length = len(prompt.strip())
    if length == 0:
        return "default"

    doc = common.load_topics()
    order = doc.get("order") or list(doc.get("topics", {}).keys())
    topics = doc.get("topics", {})

    best_key, best_score = None, 0
    for key in order:
        if key in ("default", "big_task"):
            continue
        entry = topics.get(key) or {}
        score = _score(text, entry.get("keywords") or [])
        # Stable: ties go to whichever topic comes first in `order`.
        if score > best_score:
            best_key, best_score = key, score

    if length >= VERY_LONG_PROMPT and best_score < STRONG_MATCH:
        return "big_task"
    if best_key:
        return best_key
    if length >= LONG_PROMPT:
        return "big_task"
    return "default"


def main():
    """CLI for tests and manual checks: prints only the topic key."""
    text = sys.stdin.read() if len(sys.argv) < 2 else sys.argv[1]
    sys.stdout.write(classify(text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
