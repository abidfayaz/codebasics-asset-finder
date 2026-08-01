"""
The search side - takes what you typed and finds the closest indexed content.

How it works, in plain language:
  1. Your search text is turned into a vector (the same kind of "meaning
     fingerprint" the indexer made for every slide and page).
  2. ChromaDB compares that fingerprint against every stored one and hands
     back the closest matches, best first.

Because it compares meaning rather than letters, searching "how do I price a
course" can find a slide that says "revenue model" without sharing a word.
"""

import html
import re

import indexer
import config

# Everyday words that would light up almost every result if we highlighted
# them, so we leave them alone.
STOP_WORDS = {
    "a", "an", "and", "the", "is", "are", "was", "were", "be", "been", "of",
    "in", "on", "at", "to", "for", "with", "from", "by", "as", "it", "its",
    "this", "that", "these", "those", "or", "but", "if", "then", "than",
    "so", "do", "does", "did", "how", "what", "when", "where", "which",
    "who", "why", "can", "could", "should", "would", "will", "i", "me",
    "my", "you", "your", "about", "into", "over", "any", "all",
}


def query_terms(query: str) -> list[str]:
    """
    Break the search into the words worth paying attention to: drop
    punctuation, drop everyday words, drop single letters.

    There is no limit on how many words a search may have - every one of them
    gets highlighted in the results.

    Two-letter words are kept on purpose, so short but real terms like
    "AI", "ML" and "BI" still count.
    """
    words = re.findall(r"[a-zA-Z0-9]+", (query or "").lower())
    terms = []
    for word in words:
        if len(word) >= 2 and word not in STOP_WORDS and word not in terms:
            terms.append(word)
    return terms


def matching_terms(query: str, text: str) -> list[str]:
    """Which of the search words actually appear in this text."""
    lowered = (text or "").lower()
    return [t for t in query_terms(query) if re.search(_term_pattern(t), lowered)]


def _term_pattern(term: str) -> str:
    """
    Match a whole word, allowing a simple plural. "schema" also matches
    "schemas"; it will not match "schematic".
    """
    return r"\b" + re.escape(term) + r"e?s?\b"


def highlight(text: str, query: str) -> str:
    """
    Return the text as HTML with the search words marked in yellow.

    The text is escaped first, so any stray < or & in a slide cannot break
    the page layout.
    """
    safe_text = html.escape(text or "")
    terms = query_terms(query)
    if not terms:
        return safe_text

    # One combined pattern so overlapping words are handled in a single pass.
    combined = "|".join(_term_pattern(t) for t in terms)

    def wrap(match):
        # A soft tint of the app's accent colour, matching the design.
        return (
            "<mark style='background-color:#E5E8FD; color:#312E81; "
            f"padding:1px 4px; border-radius:3px;'>{match.group(0)}</mark>"
        )

    return re.sub(combined, wrap, safe_text, flags=re.IGNORECASE)


def similarity_from_distance(distance: float) -> float:
    """
    ChromaDB reports a 'distance' - smaller means closer. Flip it into a
    'similarity' score where bigger means better, which is easier to reason
    about. Roughly: 1.0 is a perfect match, 0.0 is unrelated.
    """
    return 1.0 - distance


def is_strong(similarity: float, query: str = "", text: str = "") -> bool:
    """
    Is this a confident match?

    Two ways to qualify, and either is enough:

    1. The similarity score clears SIMILARITY_THRESHOLD - the text is about the
       same subject as the search, in the model's judgement.

    2. Every meaningful word of the search appears in the text. This matters
       because similarity compares the WHOLE search against the WHOLE passage.
       Search "sam altman" and a long paragraph that names him scores only
       about 0.34, because the name is a small part of a passage mostly about
       something else - yet it is plainly a real match. Short, exact searches
       (names, product names, error codes) are how people actually search, and
       judging them on overall similarity alone gets them wrong.
    """
    if similarity >= config.SIMILARITY_THRESHOLD:
        return True

    wanted = query_terms(query)
    if wanted and len(matching_terms(query, text)) == len(wanted):
        return True

    return False


def confidence_label(similarity: float, query: str = "", text: str = "") -> str:
    """
    Turn the score into an honest word rather than a fake-precise percentage.
    """
    return "Strong match" if is_strong(similarity, query, text) else "Close match"


def has_strong_match(results: list[dict]) -> bool:
    """
    Did anything actually clear the quality bar?

    If not, the app shows the "nothing matched exactly, here are the closest"
    fallback instead of presenting weak results as if they were good.

    Uses the same test as the label on each card, so the two can never
    disagree - the fallback banner will not appear above a card that calls
    itself a strong match.
    """
    if not results:
        return False
    return any(r["confidence"].startswith("Strong") for r in results)


def location_label(result: dict) -> str:
    """Describe where in the file the match sits, e.g. 'Slide 3' or 'Page 2'."""
    if result["type"] == "deck":
        return f"Slide {result['location']}"
    if result["type"] == "pdf":
        return f"Page {result['location']}"
    if result["type"] == "youtube":
        # A transcript is cut into excerpts of a couple of hundred words so we
        # can point at the stretch of the video where the subject comes up.
        #
        # Always shown as "excerpt 4 of 13", never "part 4". These are OUR
        # slices of the transcript - they have nothing to do with how the
        # creator numbered their own episodes, and a channel that publishes
        # "Tutorial - 1", "Tutorial - 2" makes "part 4" genuinely misleading.
        total = result.get("total_parts")
        if total:
            return f"excerpt {result['location']} of {total}"
        return f"excerpt {result['location']}"
    return ""


def type_label(file_type: str) -> str:
    """A tidy name for the file type, for showing on screen."""
    return {"deck": "Deck", "pdf": "PDF", "image": "Image",
            # Spelled out, so it is obvious this is a video on the web and not
            # a file sitting in a folder.
            "youtube": "YouTube video"}.get(file_type, file_type.title())


def count_parts(collection, file_key: str) -> int:
    """
    How many excerpts a video's transcript was cut into.

    Looked up from the index rather than stored on every chunk, so no content
    has to be fetched or re-indexed to show "excerpt 4 of 13".
    """
    try:
        found = collection.get(where={"file_key": {"$eq": file_key}})
        return len(found.get("ids") or [])
    except Exception:
        # Never let a display detail break a search.
        return 0


def local_file(result: dict):
    """
    Where this result's file actually is on THIS machine, or None if it is not
    here (a video, or an index copied from elsewhere without the files).

    The index records both the full path from the machine that built it and a
    path relative to the assets folder. The relative one is what still works
    after the index is copied to a server, so it is tried first.
    """
    from pathlib import Path

    relative = result.get("rel_path")
    if relative:
        candidate = config.ASSETS_FOLDER / relative
        if candidate.exists():
            return candidate

    stored = result.get("path") or ""
    if stored and not stored.startswith("http"):
        candidate = Path(stored)
        if candidate.exists():
            return candidate

    return None


def web_link(result: dict) -> str:
    """
    A result's web address, cleaned up and checked.

    Returns "" if what we have is not a usable web address, so the app can
    leave the link out rather than render one that goes nowhere. A link
    missing its "https://" is treated by the browser as a page on the current
    site, which is how a "Watch on YouTube" button once ended up loading the
    app's own address for ever.
    """
    raw = (result.get("path") or "").strip()
    if not raw:
        return ""

    # Windows path separators can creep in if a URL is ever handled as a file
    # path. Undo that, then repair the "https:/" that results.
    cleaned = raw.replace("\\", "/")
    if cleaned.startswith("https:/") and not cleaned.startswith("https://"):
        cleaned = cleaned.replace("https:/", "https://", 1)
    if cleaned.startswith("http:/") and not cleaned.startswith("http://"):
        cleaned = cleaned.replace("http:/", "http://", 1)

    if not cleaned.startswith(("http://", "https://")):
        return ""
    return cleaned


def display_location(result: dict) -> str:
    """
    What to show as the result's location.

    The file's own folder if we can find it, otherwise the short path inside
    the assets folder - which is honest and readable, rather than showing a
    path from somebody else's computer that means nothing here.
    """
    if result["type"] == "youtube":
        return result["path"]

    found = local_file(result)
    if found:
        return str(found)
    return result.get("rel_path") or result.get("path", "")


def make_snippet(text: str, limit: int = 400) -> str:
    """Shorten a long slide/page down to something readable on a card."""
    text = " ".join(text.split())  # collapse line breaks and double spaces
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def search(query: str, top_k: int = None, collection=None, model=None) -> list[dict]:
    """
    Find the indexed slides/pages closest in meaning to `query`.

    Returns a list of dictionaries, best match first. Each one holds:
      file_name, type, location, path, text, snippet, similarity, confidence

    `collection` and `model` can be passed in so the app can reuse ones it
    already has loaded, instead of loading them again on every search.
    """
    query = (query or "").strip()
    if not query:
        return []

    top_k = top_k or config.TOP_K
    collection = collection or indexer.get_collection()
    model = model or indexer.get_embedding_model()

    # Nothing indexed yet - return nothing rather than erroring.
    if collection.count() == 0:
        return []

    query_vector = model.encode([query]).tolist()

    raw = collection.query(
        query_embeddings=query_vector,
        # Never ask for more results than exist, or Chroma complains.
        n_results=min(top_k, collection.count()),
    )

    # Chroma wraps everything in an outer list (one entry per query). We only
    # ever send one query, so we take position 0 of each list.
    documents = raw["documents"][0]
    metadatas = raw["metadatas"][0]
    distances = raw["distances"][0]

    # How many excerpts each matched video has, worked out once per video
    # rather than once per result.
    part_totals = {}
    for meta in metadatas:
        if meta["type"] == "youtube" and meta["file_key"] not in part_totals:
            part_totals[meta["file_key"]] = count_parts(collection, meta["file_key"])

    results = []
    for text, meta, distance in zip(documents, metadatas, distances):
        similarity = similarity_from_distance(distance)
        result = {
            "file_name": meta["file_name"],
            "type": meta["type"],
            "location": meta["location"],
            "path": meta["path"],
            # Path inside the assets folder - survives the index being copied
            # to another machine, which the full path above does not.
            "rel_path": meta.get("rel_path", ""),
            # "text" if typed on the slide, "picture" if read from an image
            # sitting on it. Older entries have no source, so assume text.
            "source": meta.get("source", "text"),
            # For videos: how many excerpts the transcript was cut into.
            "total_parts": part_totals.get(meta["file_key"]),
            "text": text,
            "snippet": make_snippet(text),
            "similarity": similarity,
            "confidence": confidence_label(similarity, query, text),
        }
        result["location_label"] = location_label(result)
        results.append(result)

    return results
