"""
The indexer - the part that READS your files and makes them searchable.

What it does, in plain language:
  1. Walks through your assets folder (including sub-folders).
  2. For each PowerPoint deck, pulls the text off every slide.
     For each PDF, pulls the text off every page.
  3. Turns each slide/page of text into a "vector" (a list of numbers that
     captures its meaning) using a model that runs on your own computer.
  4. Stores those vectors in a local database (ChromaDB) so we can search
     them later by meaning rather than by filename.
  5. Writes a record of what happened to every file into index_status.json.

Hard rules:
  - It NEVER changes, moves or deletes your source files. It only reads them.
  - A broken or unreadable file NEVER stops the run. It is recorded as failed
    with a plain-English reason, and the indexer moves on to the next file.

Run it on its own with:  python indexer.py
"""

import datetime
import json
import time
import traceback
from pathlib import Path

import config
import vision

# One "chunk" = one searchable piece of content. For a deck that is one slide;
# for a PDF that is one page. Keeping chunks small is what lets a search point
# you at slide 7 rather than at a 60-slide deck.

# File types this stage handles right now.
PPTX_EXTENSIONS = {".pptx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# A short breather between videos, so YouTube does not think we are a robot
# hammering it. Fetching captions is quick, so this barely costs anything.
PAUSE_BETWEEN_VIDEOS_SECONDS = 2

# If YouTube refuses this many videos in a row, it is throttling us and every
# remaining video will be refused too. Stop and let the next run pick them up.
STOP_AFTER_BLOCKED_VIDEOS = 3

# The name of the collection (like a table) inside ChromaDB.
COLLECTION_NAME = "assets"

# Loaded once and reused, because loading the model takes a few seconds.
_embedding_model = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    """Current time as a readable text stamp, e.g. 2026-07-28T09:40:11."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _modified_time(path: Path) -> str:
    """When the file was last changed, as a readable text stamp."""
    stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    return stamp.replace(microsecond=0).isoformat()


def get_embedding_model():
    """
    Load the local text-understanding model (downloads ~80MB the first time,
    then works offline). Kept in memory so we only pay that cost once.
    """
    global _embedding_model
    if _embedding_model is None:
        # Imported here rather than at the top so that merely importing this
        # file stays fast.
        from sentence_transformers import SentenceTransformer

        print(f"Loading the embedding model ({config.EMBEDDING_MODEL})...")
        _embedding_model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _embedding_model


def get_collection():
    """Open (or create) the local vector database folder."""
    import chromadb

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    # "cosine" is the right way to compare this model's vectors.
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Reading the status file (what we already know about each file)
# ---------------------------------------------------------------------------

def load_status() -> dict:
    """Read index_status.json. Returns an empty record if it does not exist."""
    if not config.STATUS_FILE.exists():
        return {"files": {}, "last_run": None, "picture_library": {}}
    try:
        with open(config.STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("files", {})
        data.setdefault("last_run", None)
        # Descriptions of slide pictures, shared across every deck and keyed
        # by the picture's own content. Because it is keyed by the picture and
        # not by the file, renaming a deck - or holding two copies of one -
        # costs nothing: the pictures are recognised and reused.
        data.setdefault("picture_library", {})
        return data
    except (json.JSONDecodeError, OSError):
        # A corrupted status file should not stop the app - start fresh.
        print("Could not read index_status.json - starting a fresh record.")
        return {"files": {}, "last_run": None}


def save_status(status: dict) -> None:
    """Write index_status.json back to disk."""
    with open(config.STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)


# ---------------------------------------------------------------------------
# Text extraction, one function per file type
# ---------------------------------------------------------------------------

def extract_pptx_chunks(path: Path) -> list[dict]:
    """
    Pull the text out of a PowerPoint deck, one entry per slide.

    Returns a list like:
      [{"number": 1, "text": "Star schema basics ..."}, ...]
    Slides with no text at all are left out.

    TODO: slides that are just a picture (a screenshot, a diagram with no text
    box) produce no text and so cannot be found by search. Sending those
    embedded pictures through the vision model would cover them. The spec
    allows leaving this out of the prototype - see BUILD_SPEC section 4.
    """
    from pptx import Presentation

    presentation = Presentation(str(path))
    chunks = []

    for slide_number, slide in enumerate(presentation.slides, start=1):
        pieces = []
        for shape in slide.shapes:
            # Normal text boxes, titles, bullet lists.
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    pieces.append(text)
            # Tables - read them cell by cell, left to right.
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    row_text = " | ".join(c for c in cells if c)
                    if row_text:
                        pieces.append(row_text)

        slide_text = "\n".join(pieces).strip()
        if slide_text:
            chunks.append({"number": slide_number, "text": slide_text})

    return chunks


# A picture smaller than this in either direction is decoration - a bullet
# icon, a divider, a logo corner. Not worth a vision call.
MIN_PICTURE_SIDE = 200


def _collect_pictures(shapes, slide_number: int, found: list) -> None:
    """
    Walk a slide's shapes and gather every picture, including pictures that
    are nested inside grouped shapes.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _collect_pictures(shape.shapes, slide_number, found)
        elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            found.append((slide_number, shape))


def extract_pptx_pictures(path: Path) -> list[dict]:
    """
    Find the pictures sitting on each slide of a deck.

    Returns a list like:
      [{"number": 3, "blob": b"...", "sha1": "..."}, ...]

    Two savings are applied before anything is sent anywhere:
      - tiny pictures are ignored (icons, dividers, logo corners)
      - a picture used on several slides is only returned once, so a logo
        repeated on every slide costs one look instead of fifty
    """
    from pptx import Presentation

    presentation = Presentation(str(path))

    found = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        _collect_pictures(slide.shapes, slide_number, found)

    pictures = []
    seen = set()
    for slide_number, shape in found:
        try:
            image = shape.image
        except Exception:
            # Linked or unreadable picture - nothing we can send.
            continue

        # Already seen this exact picture elsewhere in the deck?
        if image.sha1 in seen:
            continue

        width, height = image.size
        if width < MIN_PICTURE_SIDE or height < MIN_PICTURE_SIDE:
            continue

        seen.add(image.sha1)
        pictures.append({
            "number": slide_number,
            "blob": image.blob,
            "sha1": image.sha1,
        })

    return pictures


def describe_pptx_pictures(path: Path, remembered: dict,
                           budget: int = None) -> tuple[list[dict], int, dict, int]:
    """
    Send each of a deck's pictures to the vision model, and turn the answers
    into chunks tied to the slide the picture sits on.

    This is the second pass. The slide's typed text has already been read for
    free; only the pictures cost anything.

    `remembered` holds descriptions from previous runs, keyed by the picture's
    content fingerprint. Anything already in there is reused rather than sent
    again - so if a run is cut short by the free-tier limit, the next one only
    pays for the handful of pictures that are still missing, not the whole deck.

    `budget` caps how many NEW pictures may be sent to the vision model in this
    call. Anything beyond the budget is left for a later run. Pictures we
    already remember are free and never count against it.

    Returns (chunks, how many pictures are still unread, updated memory, how
    much of the budget was used). A picture that fails is skipped rather than
    failing the whole deck, but is counted so the deck can be marked as needing
    another go. A DECORATIVE picture is not a failure: it was read, and
    deliberately left out.
    """
    if not vision.is_configured():
        return [], 0, remembered, 0, 0

    pictures = extract_pptx_pictures(path)
    if not pictures:
        return [], 0, remembered, 0, 0

    memory = dict(remembered or {})
    already = sum(1 for p in pictures if p["sha1"] in memory)
    todo = len(pictures) - already

    if already:
        print(f"          {len(pictures)} picture(s): {already} already known, "
              f"{todo} to look at")
    else:
        print(f"          {len(pictures)} picture(s) to look at")

    chunks = []
    unread = 0
    used = 0        # budget spent - a refused picture still costs an attempt
    read = 0        # pictures actually understood

    for position, picture in enumerate(pictures, start=1):
        fingerprint = picture["sha1"]

        if fingerprint in memory:
            description = memory[fingerprint]     # free: we did this already
        else:
            # Out of allowance for this run - leave the rest for next time.
            if budget is not None and used >= budget:
                unread += 1
                continue
            try:
                description = vision.describe_image(picture["blob"])
            except Exception as error:
                unread += 1
                used += 1
                print(f"          picture on slide {picture['number']}: {error}")
                time.sleep(vision.PAUSE_BETWEEN_IMAGES_SECONDS)
                continue
            memory[fingerprint] = description
            used += 1
            read += 1
            print(f"          read picture on slide {picture['number']}")
            time.sleep(vision.PAUSE_BETWEEN_IMAGES_SECONDS)

        if not vision.is_decorative(description):
            chunks.append({
                "number": picture["number"],
                "text": description,
                # A slide can hold typed text AND several pictures, so each
                # piece needs its own name inside the index.
                "id_part": f"{picture['number']}-picture{position}",
                "source": "picture",
            })

    return chunks, unread, memory, used, read


def extract_pdf_chunks(path: Path) -> list[dict]:
    """
    Pull the text out of a PDF, one entry per page.
    Pages with no text (e.g. pure images) are left out.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))

    # An encrypted PDF cannot be read without the password.
    if reader.is_encrypted:
        raise ValueError("the PDF is password-protected")

    chunks = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            # One bad page should not lose the whole document.
            text = ""
        if text:
            chunks.append({"number": page_number, "text": text})

    return chunks


# ---------------------------------------------------------------------------
# The main routine
# ---------------------------------------------------------------------------

def remove_missing_files(collection, status: dict) -> list[str]:
    """
    Forget about files that are no longer on disk.

    When a file is deleted or renamed, its old entries would otherwise linger
    for ever - showing up in search results that point at a path which does not
    exist, and inflating the counts on the Processing log.

    This ONLY removes index entries. It never touches a real file. And it only
    removes an entry when the file it points at is genuinely missing.

    Returns the names of the files that were forgotten.
    """
    recorded = list(status["files"].items())
    if not recorded:
        return []

    missing = []
    for file_key, record in recorded:
        # Videos live on the web, not on disk. Their "path" is a link, which
        # will never exist as a file - checking it would delete every video.
        if record.get("type") == "youtube":
            continue

        stored_path = record.get("path", "")
        # No path recorded means we cannot check - leave it well alone.
        if not stored_path:
            continue
        if not Path(stored_path).exists():
            missing.append((file_key, record))

    # Safety net: if EVERY known file looks missing, the folder itself is
    # probably unavailable - an unplugged drive, or OneDrive mid-sync. Wiping
    # the whole index in that situation would be a disaster, so we refuse and
    # say why. A genuine "delete everything" is rare; a disconnected folder is
    # not.
    if missing and len(missing) == len(recorded) and len(recorded) >= 5:
        print(
            f"  WARNING: all {len(recorded)} known files appear to be missing. "
            f"That usually means the assets folder is unavailable rather than "
            f"emptied, so nothing was removed from the index. Check that "
            f"{config.ASSETS_FOLDER} is reachable, then run again."
        )
        return []

    removed = []
    for file_key, record in missing:
        # Drop its searchable pieces, then drop its row in the status record.
        collection.delete(where={"file_key": {"$eq": file_key}})
        del status["files"][file_key]
        removed.append(record.get("file_name") or file_key)
        print(f"  REMOVED {file_key}  (file no longer on disk)")

    return removed


def index_pictures_only(limit: int = 3) -> dict:
    """
    Work through the slide pictures that are still unread, a few at a time.

    Nothing else runs: no other files are touched, no videos are fetched. Only
    decks that still have unread pictures are looked at, and only `limit` new
    pictures are sent to the vision model in total - so you can chip away at
    the backlog in small, quick batches instead of one long run.

    Pictures already described in an earlier run are reused from memory and
    cost nothing, so nothing is ever read twice.
    """
    status = load_status()
    collection = get_collection()
    vision.reset_give_up_counter()

    summary = {
        "decks_touched": 0,
        "pictures_read": 0,
        "pictures_refused": 0,
        "pictures_still_unread": 0,
        "decks_finished": 0,
        "chunks_indexed": 0,
        "remaining_decks": [],
    }

    # First: forget anything that has been deleted or renamed away, so a stale
    # entry cannot linger in search results.
    gone = remove_missing_files(collection, status)
    for name in gone:
        print(f"  removed from the index: {name} (no longer on disk)")
    if gone:
        save_status(status)

    # Every deck on disk, so a newly added or renamed file is picked up too.
    decks_on_disk = sorted(
        path for path in config.ASSETS_FOLDER.rglob("*")
        if path.is_file() and path.suffix.lower() in PPTX_EXTENSIONS
    )

    outstanding = []
    for path in decks_on_disk:
        file_key = str(path.relative_to(config.ASSETS_FOLDER)).replace("\\", "/")
        record = status["files"].get(file_key)
        if record is None:
            # New to us - never seen, or seen under an older name.
            record = {
                "file_name": path.name,
                "path": str(path),
                "type": "deck",
                "status": "in_progress",
                "reason": None,
                "chunks": 0,
                "modified_time": _modified_time(path),
                "indexed_time": None,
                "pictures_unread": None,   # unknown until we look
            }
            status["files"][file_key] = record
            outstanding.append((file_key, record))
        elif record.get("pictures_unread") or record.get("status") != "processed":
            outstanding.append((file_key, record))

    # Smallest backlog first, so whole decks get finished off early. A deck we
    # have not counted yet goes last.
    outstanding.sort(key=lambda pair: pair[1].get("pictures_unread") or 999)

    if not outstanding:
        print("No decks have unread pictures. Nothing to do.")
        return summary

    print(f"{len(outstanding)} deck(s) still have unread pictures. "
          f"Reading up to {limit} picture(s) this run.\n")

    budget = limit

    for file_key, record in outstanding:
        path = Path(record["path"])
        if not path.exists():
            continue

        print(f"  {record['file_name'][:58]}")

        # The typed text is free to re-read, and we need it so the deck's
        # pieces can be stored complete.
        text_chunks = extract_pptx_chunks(path)

        # The shared library, plus anything this deck remembered on its own
        # from before the library existed.
        library = status["picture_library"]
        library.update(record.get("picture_memory") or {})

        picture_chunks, unread, library, used, read = describe_pptx_pictures(
            path, library, budget=budget)
        status["picture_library"] = library
        budget -= used

        stored = _store_chunks(collection, file_key, path, "deck",
                               text_chunks + picture_chunks,
                               record.get("modified_time", ""))

        record.update({
            "status": "processed",
            "chunks": stored,
            "pictures_unread": unread,
            "indexed_time": _now(),
            "reason": (f"{unread} picture(s) still to read - run again to "
                       f"continue" if unread else None),
        })
        # The per-deck copy has been folded into the shared library.
        record.pop("picture_memory", None)

        summary["decks_touched"] += 1
        summary["pictures_read"] += read
        summary["pictures_refused"] += (used - read)
        summary["pictures_still_unread"] += unread
        summary["chunks_indexed"] += stored
        if not unread:
            summary["decks_finished"] += 1
            print(f"      done - all pictures read ({stored} pieces)")
        else:
            summary["remaining_decks"].append((record["file_name"], unread))
            refused = f", {used - read} refused" if used > read else ""
            print(f"      {read} read{refused}, {unread} still to go "
                  f"({stored} pieces)")

        save_status(status)

    status["last_run"] = _now()
    save_status(status)
    return summary


def index_videos_only(limit: int = None, force: bool = False) -> dict:
    """
    Index ONLY the YouTube videos, and nothing else.

    No files are read, no pictures are sent anywhere, nothing to do with the
    vision model runs. Useful because fetching transcripts takes a couple of
    minutes, while reading pictures can take half an hour - there is no reason
    for one to wait on the other.

    `limit` stops after that many videos, for trying it out on a few first.
    """
    status = load_status()
    collection = get_collection()

    summary = {
        "videos_indexed": 0,
        "videos_skipped": 0,
        "videos_failed": 0,
        "videos_unchanged": 0,
        "chunks_indexed": 0,
        "skipped_files": [],
        "video_failures": [],
    }

    index_videos(collection, status, summary, force=force, limit=limit)

    status["last_run"] = _now()
    save_status(status)
    return summary


def index_videos(collection, status: dict, summary: dict,
                 force: bool = False, limit: int = None) -> None:
    """
    Make the YouTube videos listed in public_links.xlsx searchable by what is
    said in them.

    For each video: fetch its public captions, split them into parts, and store
    those parts exactly like slides or pages - the only differences are that
    the "path" is a web link rather than a folder, and the type is "youtube".

    Videos already done are left alone, so re-running does not fetch every
    transcript again. A video with no usable captions is recorded as skipped,
    with the reason, and never stops the run.
    """
    import links
    import transcripts

    videos, _ = links.get_youtube_links()
    if not videos:
        return

    total = len(videos)
    if limit:
        videos = videos[:limit]
        print(f"\nChecking {len(videos)} of {total} YouTube video(s) "
              f"(stopping early, as asked).")
    else:
        print(f"\nChecking {total} YouTube video(s) from the link list.")

    blocked_in_a_row = 0
    # The spreadsheet can list the same video twice - once as a long
    # youtube.com link and once as a short youtu.be one. Both resolve to the
    # same video, so we handle it once and ignore the second listing entirely.
    # Without this the totals count its parts twice.
    handled_this_run = set()

    for video in videos:
        url = video["url"]
        title = video["title"] or url

        try:
            identifier = transcripts.video_id(url)
        except transcripts.TranscriptUnavailable as problem:
            identifier = None

        # Videos are keyed by their YouTube id, so the same video listed twice
        # under different link styles is still only one entry.
        video_key = f"youtube:{identifier or url}"

        # Same video listed under a second URL - already dealt with above.
        if video_key in handled_this_run:
            summary["videos_duplicate"] = summary.get("videos_duplicate", 0) + 1
            continue
        handled_this_run.add(video_key)

        previous = status["files"].get(video_key)

        # Already dealt with? Leave it. There is no "modified time" for a
        # video, so anything we have already fetched counts as done until you
        # ask for a forced rebuild.
        if not force and previous and previous.get("status") in ("processed", "skipped"):
            summary["videos_unchanged"] += 1
            summary["chunks_indexed"] += previous.get("chunks", 0)
            continue

        record = {
            "file_name": title,
            "path": url,
            "type": "youtube",
            "status": "in_progress",
            "reason": None,
            "chunks": 0,
            "modified_time": None,
            "indexed_time": None,
        }
        status["files"][video_key] = record
        save_status(status)

        try:
            result = transcripts.fetch_transcript(url)
        except transcripts.TranscriptUnavailable as problem:
            # Two very different situations, and they must not be recorded the
            # same way:
            #   "skipped" - a fact about the video (no captions). Settled;
            #               we will not keep asking.
            #   "failed"  - a passing problem (YouTube throttling us). Left
            #               unfinished on purpose, so the next run tries again.
            temporary = getattr(problem, "temporary", False)
            record.update({
                "status": "failed" if temporary else "skipped",
                "reason": str(problem),
                "indexed_time": _now(),
            })
            if temporary:
                summary["videos_failed"] += 1
                summary["video_failures"].append((title, str(problem)))
                print(f"  RETRY LATER  {title[:55]}")
            else:
                summary["videos_skipped"] += 1
                summary["skipped_files"].append((title, str(problem)))
                print(f"  SKIP    {title[:60]}")
            print(f"          {problem}")
            save_status(status)

            # If YouTube is refusing us, every remaining video will be refused
            # too. Stop asking rather than working through the whole list.
            if temporary:
                blocked_in_a_row += 1
                if blocked_in_a_row >= STOP_AFTER_BLOCKED_VIDEOS:
                    print(f"\n  YouTube is refusing requests. Stopping here - "
                          f"the rest are recorded as still to do. "
                          f"Try again in a little while.")
                    return
            time.sleep(PAUSE_BETWEEN_VIDEOS_SECONDS)
            continue

        parts = transcripts.split_into_parts(result["text"])
        chunks = [
            {
                "number": number,
                "text": part,
                "id_part": f"part{number}",
                "source": "transcript",
            }
            for number, part in enumerate(parts, start=1)
        ]

        # The URL is passed as plain text, NOT as a Path - see _store_chunks.
        stored = _store_chunks(collection, video_key, url, "youtube",
                               chunks, "", display_name=title)

        record.update({
            "status": "processed",
            "reason": None,
            "chunks": stored,
            "indexed_time": _now(),
            "captions": ("auto-generated" if result["generated"]
                         else "written by the creator"),
        })
        summary["videos_indexed"] += 1
        summary["chunks_indexed"] += stored
        words = len(result["text"].split())
        print(f"  OK      {title[:60]}")
        print(f"          {words:,} words -> {stored} part(s)")
        save_status(status)
        time.sleep(PAUSE_BETWEEN_VIDEOS_SECONDS)


def plain_failure_reason(error: Exception, file_type: str) -> str:
    """
    Turn a technical error into something a person can act on.

    "Stream has ended unexpectedly" tells you nothing. "This PDF looks damaged"
    tells you to go and look at the file.
    """
    # Reasons we raised ourselves are already in plain English - keep them.
    if isinstance(error, ValueError):
        return str(error)

    name = error.__class__.__name__
    text = str(error).lower()

    if file_type == "pdf":
        if "header" in text or "stream has ended" in text or "eof" in text:
            return ("could not read this PDF - the file looks damaged, or it "
                    "is not really a PDF")
        return f"could not read this PDF ({name})"

    if file_type == "deck":
        if "package not found" in text or "not a zip" in text:
            return ("could not open this deck - the file looks damaged, or it "
                    "is not really a PowerPoint file")
        return f"could not open this deck ({name})"

    if "permission" in text or isinstance(error, PermissionError):
        return "could not open the file - it may be open in another program"

    return f"{name}: {str(error)[:120]}"


def _relative_path(path, file_type: str) -> str:
    """
    Where this file sits inside the assets folder, e.g. "Decks/svm.pptx".

    Stored alongside the full path so the index stays usable when it is copied
    to another machine - the full path would point nowhere there. Videos have
    no file, so they get an empty string.
    """
    if file_type == "youtube":
        return ""
    try:
        return str(Path(path).resolve().relative_to(config.ASSETS_FOLDER)).replace("\\", "/")
    except Exception:
        return Path(path).name


def _store_chunks(collection, file_key: str, path, file_type: str,
                  chunks: list[dict], modified: str,
                  display_name: str = None) -> int:
    """
    Turn each chunk into a vector and save it, replacing anything previously
    stored for this file. Returns how many chunks were stored.
    """
    # Remove old entries for this file first, so re-indexing a changed file
    # does not leave stale copies behind.
    collection.delete(where={"file_key": {"$eq": file_key}})

    if not chunks:
        return 0

    model = get_embedding_model()
    texts = [c["text"] for c in chunks]
    # TODO: the model only reads roughly the first 256 words of a chunk. Very
    # dense slides/pages are therefore only partly represented. Splitting long
    # pages into smaller chunks would fix this - not needed for the prototype.
    vectors = model.encode(texts).tolist()

    indexed_at = _now()
    ids = []
    metadatas = []
    for chunk in chunks:
        # A stable, unique name for this chunk, e.g. "Decks/svm.pptx::3" for
        # slide 3's text, or "...::3-picture1" for a picture on that slide.
        ids.append(f"{file_key}::{chunk.get('id_part', chunk['number'])}")
        metadatas.append({
            "file_key": file_key,
            # A video's name is its title, not the last part of a web address.
            "file_name": display_name or Path(path).name,
            # A web address must be stored exactly as written. Passing one
            # through Path() on Windows turns "https://youtu.be/x" into
            # "https:\youtu.be\x", which a browser does not recognise as a web
            # address at all - it reads it as a page on the current site.
            "path": str(path),
            # The same location, written relative to the assets folder. The
            # full path above is only meaningful on the machine that did the
            # indexing; this one still works if the index is copied elsewhere,
            # such as onto a server running a different operating system.
            "rel_path": _relative_path(path, file_type),
            "type": file_type,
            # For a deck this is the slide number; for a PDF the page number.
            "location": chunk["number"],
            # "text" = typed on the slide, "picture" = read from an image.
            "source": chunk.get("source", "text"),
            "modified_time": modified,
            "indexed_time": indexed_at,
        })

    collection.add(ids=ids, embeddings=vectors, documents=texts,
                   metadatas=metadatas)
    return len(chunks)


def index_assets(folder=None, force: bool = False) -> dict:
    """
    Read every supported file in `folder` and make it searchable.

    force=False (the default) skips files that have not changed since the last
    run, so a second run is fast. force=True re-reads everything.

    Returns a summary dictionary, and also writes index_status.json.
    """
    folder = Path(folder) if folder else config.ASSETS_FOLDER

    if not folder.exists():
        raise FileNotFoundError(
            f"The assets folder does not exist: {folder}\n"
            f"Create it, or point ASSETS_FOLDER in your .env at the right place."
        )

    status = load_status()
    collection = get_collection()
    # Fresh run: start willing to ask the vision service again, however badly
    # the last run ended.
    vision.reset_give_up_counter()

    # Every file in the folder and all sub-folders, in a predictable order.
    all_files = sorted(p for p in folder.rglob("*") if p.is_file())

    summary = {
        "total_files": len(all_files),
        "processed": 0,
        "skipped_unchanged": 0,
        "decorative_images": 0,
        "skipped_unsupported": 0,
        "failed": 0,
        "removed_missing": 0,
        "partial": 0,
        "videos_indexed": 0,
        "videos_skipped": 0,
        "videos_failed": 0,
        "videos_unchanged": 0,
        "video_failures": [],
        "chunks_indexed": 0,
        "failures": [],       # (file name, reason) - something actually broke
        "skipped_files": [],  # (file name, reason) - a type we do not handle
        "removed_files": [],  # names of files that have gone from the disk
        "partial_files": [],  # (file name, pictures still unread)
    }

    print(f"Scanning {folder}")
    print(f"Found {len(all_files)} files.\n")

    # First, forget anything that has been deleted or renamed since last time.
    # Doing this before reading means a renamed file is cleanly replaced rather
    # than appearing twice.
    removed = remove_missing_files(collection, status)
    if removed:
        summary["removed_missing"] = len(removed)
        summary["removed_files"] = removed
        save_status(status)

    for path in all_files:
        # A short name relative to the assets folder - used as the file's ID
        # and shown in the processing log.
        file_key = str(path.relative_to(folder)).replace("\\", "/")
        extension = path.suffix.lower()
        modified = _modified_time(path)

        previous = status["files"].get(file_key)

        # --- Skip files we have already done and that have not changed ------
        # "Done" means fully done: a deck where some pictures could not be read
        # is left out of this, so the next run picks it up and tries again.
        if (not force and previous
                and previous.get("status") == "processed"
                and not previous.get("pictures_unread")
                and previous.get("modified_time") == modified):
            summary["skipped_unchanged"] += 1
            summary["chunks_indexed"] += previous.get("chunks", 0)
            continue

        # --- Images: shown to the vision model ------------------------------
        if extension in IMAGE_EXTENSIONS:
            status["files"][file_key] = {
                "file_name": path.name,
                "path": str(path),
                "type": "image",
                "status": "in_progress",
                "reason": None,
                "chunks": 0,
                "modified_time": modified,
                "indexed_time": None,
            }
            save_status(status)

            try:
                description = vision.describe_image(path)

                if vision.is_decorative(description):
                    # A logo or plain decoration. Recorded as done, but left
                    # out of the search index so it cannot clutter results.
                    collection.delete(where={"file_key": {"$eq": file_key}})
                    status["files"][file_key].update({
                        "status": "processed",
                        "reason": "decorative image - recorded but not indexed",
                        "chunks": 0,
                        "indexed_time": _now(),
                    })
                    summary["decorative_images"] += 1
                    print(f"  SKIP    {file_key}  (decorative)")
                else:
                    stored = _store_chunks(
                        collection, file_key, path, "image",
                        [{"number": 0, "text": description}], modified,
                    )
                    status["files"][file_key].update({
                        "status": "processed",
                        "reason": None,
                        "chunks": stored,
                        "indexed_time": _now(),
                    })
                    summary["processed"] += 1
                    summary["chunks_indexed"] += stored
                    print(f"  OK      {file_key}  (described)")

            except Exception as error:
                reason = str(error) or error.__class__.__name__
                status["files"][file_key].update({
                    "status": "failed",
                    "reason": reason,
                    "chunks": 0,
                    "indexed_time": _now(),
                })
                summary["failed"] += 1
                summary["failures"].append((file_key, reason))
                print(f"  FAILED  {file_key}  -> {reason}")

            save_status(status)
            # Breathe between images so the free tier does not cut us off.
            time.sleep(vision.PAUSE_BETWEEN_IMAGES_SECONDS)
            continue

        # --- Anything that is not a deck or a PDF ---------------------------
        # These are SKIPPED, not failed. Nothing is broken about a .zip - we
        # simply do not handle that type. Calling it a failure would suggest
        # the file needs fixing, which it does not.
        if extension not in PPTX_EXTENSIONS | PDF_EXTENSIONS:
            if path.name.lower() == "public_links.xlsx":
                # Not indexed as a document, but far from ignored: this is the
                # list the YouTube videos come from.
                reason = "link list - read separately to find the YouTube videos"
            else:
                reason = f"unsupported file type ({extension or 'no extension'})"
            status["files"][file_key] = {
                "file_name": path.name,
                "path": str(path),
                "type": extension.lstrip(".") or "unknown",
                "status": "skipped",
                "reason": reason,
                "chunks": 0,
                "modified_time": modified,
                "indexed_time": None,
            }
            summary["skipped_unsupported"] += 1
            summary["skipped_files"].append((file_key, reason))
            continue

        # --- Mark as in-progress and save, so a crash mid-file is visible ---
        file_type = "deck" if extension in PPTX_EXTENSIONS else "pdf"
        status["files"][file_key] = {
            "file_name": path.name,
            "path": str(path),
            "type": file_type,
            "status": "in_progress",
            "reason": None,
            "chunks": 0,
            "modified_time": modified,
            "indexed_time": None,
        }
        save_status(status)

        # --- Read it ---------------------------------------------------------
        try:
            picture_chunks = []
            pictures_unread = 0
            picture_memory = None
            if file_type == "deck":
                # Pass one: the typed text on each slide. Free.
                chunks = extract_pptx_chunks(path)
                unit = "slides"
                # Pass two: the pictures on each slide, which is what makes a
                # screenshot-only slide findable. This one costs vision calls,
                # so it runs second and only on files we are indexing anyway.
                # Reuse anything we already learned about this deck's pictures
                # on an earlier run, so a retry only pays for what is missing.
                known = (previous or {}).get("picture_memory", {})
                picture_chunks, pictures_unread, picture_memory, _used = (
                    describe_pptx_pictures(path, known))
                chunks = chunks + picture_chunks
            else:
                chunks = extract_pdf_chunks(path)
                unit = "pages"

            if not chunks:
                # The file opened fine but held nothing we could read.
                if file_type == "pdf":
                    reason = ("no readable text - the PDF may be a scan or "
                              "made of images")
                else:
                    reason = ("nothing readable found - no text on any slide, "
                              "and no pictures we could describe")
                raise ValueError(reason)

            stored = _store_chunks(collection, file_key, path, file_type,
                                   chunks, modified)

            # If some pictures could not be read - usually the free tier
            # cutting us off - the deck is only partly done. Record that, so
            # the next run comes back to it instead of skipping it as
            # finished. Without this, a throttled run would look complete for
            # ever and those slides would stay unsearchable.
            note = None
            if pictures_unread:
                note = (f"{pictures_unread} picture(s) could not be read - "
                        f"run indexing again to retry them")

            status["files"][file_key].update({
                "status": "processed",
                "reason": note,
                "chunks": stored,
                "pictures_unread": pictures_unread,
                "indexed_time": _now(),
            })
            if picture_memory:
                # What we learned about each picture, so a later run does not
                # have to ask about them again.
                status["files"][file_key]["picture_memory"] = picture_memory
            summary["processed"] += 1
            summary["chunks_indexed"] += stored
            if pictures_unread:
                summary["partial"] += 1
                summary["partial_files"].append((file_key, pictures_unread))
            extra = (f", {len(picture_chunks)} from pictures"
                     if picture_chunks else "")
            warn = f"  [{pictures_unread} picture(s) unread]" if pictures_unread else ""
            print(f"  OK      {file_key}  ({stored} pieces from {unit}{extra}){warn}")

        except Exception as error:
            # Never crash the run. Record a readable reason and carry on.
            reason = plain_failure_reason(error, file_type)
            status["files"][file_key].update({
                "status": "failed",
                "reason": reason,
                "chunks": 0,
                "indexed_time": _now(),
            })
            summary["failed"] += 1
            summary["failures"].append((file_key, reason))
            print(f"  FAILED  {file_key}  -> {reason}")
            # Full technical detail, in case you ever need it.
            traceback.print_exc(limit=1)

        save_status(status)

    # Files done. Now the videos listed in the link spreadsheet.
    try:
        index_videos(collection, status, summary, force=force)
    except Exception as error:
        # A problem reaching YouTube must not lose the file indexing we have
        # just done, so this is caught and reported rather than raised.
        print(f"\n  Could not process the video list: {error}")

    status["last_run"] = _now()
    save_status(status)
    return summary


def print_summary(summary: dict) -> None:
    """Print the result of a run in a readable shape."""
    print("\n" + "=" * 58)
    print("INDEXING SUMMARY")
    print("=" * 58)
    print(f"  Files found in folder : {summary['total_files']}")
    print(f"  Newly indexed         : {summary['processed']}")
    print(f"  Unchanged (skipped)   : {summary['skipped_unchanged']}")
    print(f"  Decorative (not indexed): {summary['decorative_images']}")
    print(f"  Skipped (unsupported) : {summary['skipped_unsupported']}")
    print(f"  Failed (real errors)  : {summary['failed']}")
    print(f"  Removed (file gone)   : {summary['removed_missing']}")
    print(f"  Videos indexed        : {summary['videos_indexed']}")
    print(f"  Videos unchanged      : {summary['videos_unchanged']}")
    print(f"  Videos skipped        : {summary['videos_skipped']}")
    if summary.get("videos_duplicate"):
        print(f"  Videos listed twice   : {summary['videos_duplicate']} "
              f"(same video, second URL ignored)")
    print(f"  Searchable chunks     : {summary['chunks_indexed']}")

    if summary["removed_files"]:
        print("\n  Forgotten - these files are no longer on disk:")
        for name in summary["removed_files"]:
            print(f"    - {name}")

    if summary["partial_files"]:
        print("\n  Only partly read - run indexing again to finish these:")
        for name, unread in summary["partial_files"]:
            print(f"    - {name}  ({unread} picture(s) still unread)")

    if summary["failures"]:
        print("\n  Files that could not be read (something is wrong with them):")
        for name, reason in summary["failures"]:
            print(f"    - {name}\n        reason: {reason}")

    if summary["skipped_files"]:
        print("\n  Files skipped on purpose (types we do not handle):")
        for name, reason in summary["skipped_files"]:
            print(f"    - {name}\n        reason: {reason}")
    print("=" * 58)
    print(f"\nDetails written to: {config.STATUS_FILE}")


def print_video_summary(summary: dict) -> None:
    """Print the result of a videos-only run."""
    print("\n" + "=" * 58)
    print("YOUTUBE SUMMARY")
    print("=" * 58)
    print(f"  Newly indexed     : {summary['videos_indexed']}")
    print(f"  Already done      : {summary['videos_unchanged']}")
    print(f"  Skipped for good  : {summary['videos_skipped']}")
    print(f"  To retry later    : {summary['videos_failed']}")
    print(f"  Searchable parts  : {summary['chunks_indexed']}")

    if summary["skipped_files"]:
        print("\n  Skipped - nothing to fetch:")
        for name, reason in summary["skipped_files"]:
            print(f"    - {name}\n        {reason}")

    if summary["video_failures"]:
        print("\n  Not done yet - run again later to pick these up:")
        for name, reason in summary["video_failures"]:
            print(f"    - {name}\n        {reason}")
    print("=" * 58)


if __name__ == "__main__":
    import sys

    arguments = sys.argv[1:]

    if "--pictures-only" in arguments:
        # Just the outstanding slide pictures, a few at a time.
        batch = 3
        if "--limit" in arguments:
            batch = int(arguments[arguments.index("--limit") + 1])
        result = index_pictures_only(limit=batch)
        print("\n" + "=" * 58)
        print("PICTURE CATCH-UP SUMMARY")
        print("=" * 58)
        print(f"  Decks worked on     : {result['decks_touched']}")
        print(f"  Pictures read now   : {result['pictures_read']}")
        if result["pictures_refused"]:
            print(f"  Refused by Groq     : {result['pictures_refused']} "
                  f"(free allowance used up - try later)")
        print(f"  Decks now complete  : {result['decks_finished']}")
        print(f"  Pictures still to do: {result['pictures_still_unread']}")
        if result["remaining_decks"]:
            print("\n  Not reached this run:")
            for name, left in result["remaining_decks"]:
                print(f"    - {name[:52]}  ({left} picture(s) left)")
        print("=" * 58)

    elif "--videos-only" in arguments:
        # Just the YouTube transcripts. Nothing else is touched.
        count = None
        if "--limit" in arguments:
            count = int(arguments[arguments.index("--limit") + 1])
        print_video_summary(
            index_videos_only(limit=count, force="--force" in arguments))
    else:
        print_summary(index_assets(force="--force" in arguments))
