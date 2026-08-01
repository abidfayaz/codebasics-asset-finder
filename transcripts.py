"""
Fetches the captions (subtitles) of a public YouTube video.

What this gives us: the words *spoken* in a video, as text we can search. It
does NOT see anything on screen - a slide shown silently in a video will not be
found this way.

How it works: YouTube publishes captions for most videos, either typed by the
creator or produced automatically by its speech recognition. This module asks
for those captions and joins them into one block of text.

Free, and no API key. It reads the same public captions your browser can show.

Run it on its own to try one video:
    python transcripts.py                 (uses the first YouTube link found)
    python transcripts.py <youtube-url>   (tries a specific video)
"""

import re

# Which caption languages to accept, in order of preference. English first,
# then plain "en" variants that YouTube uses for auto-generated tracks.
PREFERRED_LANGUAGES = ["en", "en-US", "en-GB"]


class TranscriptUnavailable(Exception):
    """
    Raised when a video's captions could not be read. Not a crash.

    `temporary` says whether it is worth trying again later:
      False - a fact about the video (no captions, owner disabled them,
              video deleted). Trying again tomorrow changes nothing.
      True  - a passing problem (YouTube throttling us, network trouble).
              The same video will very likely work later.
    """

    def __init__(self, message, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary


def video_id(url: str) -> str:
    """
    Pull the video's ID out of a YouTube link.

    Handles both shapes used in your spreadsheet:
        https://www.youtube.com/watch?v=M4ztKyNkDIM
        https://youtu.be/gmvvaobm7eQ?si=lBwDs3zf4ZQ_1Buu
    """
    url = (url or "").strip()

    # Long form: ...watch?v=VIDEOID&other=stuff
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)

    # Short form: youtu.be/VIDEOID?si=...
    match = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)

    # Embed form, just in case: /embed/VIDEOID or /shorts/VIDEOID
    match = re.search(r"/(?:embed|shorts|v)/([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)

    raise TranscriptUnavailable(f"could not find a video ID in the link: {url}")


def fetch_transcript(url: str) -> dict:
    """
    Get the spoken words of one video.

    Returns a dictionary:
        {"video_id": ..., "text": ..., "language": ..., "generated": True/False,
         "segments": how many caption lines were joined}

    Raises TranscriptUnavailable, with a plain-English reason, when there are
    no captions to be had. The caller decides what to do - nothing here crashes
    the program.
    """
    from youtube_transcript_api import (
        YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound,
        VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId,
        IpBlocked, RequestBlocked, CouldNotRetrieveTranscript,
    )

    identifier = video_id(url)
    api = YouTubeTranscriptApi()

    try:
        # Ask what caption tracks exist, so we can say which one we used and
        # whether YouTube generated it automatically.
        available = api.list(identifier)
        try:
            track = available.find_manually_created_transcript(PREFERRED_LANGUAGES)
        except NoTranscriptFound:
            track = available.find_generated_transcript(PREFERRED_LANGUAGES)

        fetched = track.fetch()

    except TranscriptsDisabled:
        raise TranscriptUnavailable(
            "captions are switched off for this video by its owner")
    except NoTranscriptFound:
        raise TranscriptUnavailable(
            "this video has no captions in English")
    except (VideoUnavailable, InvalidVideoId):
        raise TranscriptUnavailable(
            "the video could not be found - it may be private or deleted")
    except VideoUnplayable:
        raise TranscriptUnavailable("this video cannot be played")
    except AgeRestricted:
        raise TranscriptUnavailable(
            "this video is age-restricted, so its captions cannot be read")
    except (IpBlocked, RequestBlocked):
        # Passing problem: YouTube has decided we are asking too often.
        raise TranscriptUnavailable(
            "YouTube is refusing requests from this computer for now - "
            "try again later", temporary=True)
    except CouldNotRetrieveTranscript as error:
        raise TranscriptUnavailable(
            f"captions could not be read ({error.__class__.__name__})")
    except Exception as error:
        # Anything unrecognised is treated as passing, so it gets another go
        # rather than being written off for ever.
        raise TranscriptUnavailable(
            f"unexpected problem reading captions ({error.__class__.__name__})",
            temporary=True)

    # Each caption line is a short fragment; join them into readable text.
    pieces = [snippet.text.strip() for snippet in fetched if snippet.text.strip()]
    text = " ".join(pieces)
    # Captions often contain [Music], [Applause] and similar stage directions.
    text = re.sub(r"\[[A-Za-z ]{1,20}\]", " ", text)
    text = " ".join(text.split())

    if not text:
        raise TranscriptUnavailable("the captions for this video are empty")

    return {
        "video_id": identifier,
        "text": text,
        "language": getattr(track, "language_code", "?"),
        "generated": bool(getattr(track, "is_generated", False)),
        "segments": len(pieces),
    }


# A whole transcript is far too long to search as one lump - the model that
# turns text into numbers only reads about 256 words. Splitting a video into
# parts means a search can point at the bit of the video that actually talks
# about your subject.
WORDS_PER_PART = 180
OVERLAP_WORDS = 30


def split_into_parts(text: str, words_per_part: int = WORDS_PER_PART,
                     overlap: int = OVERLAP_WORDS) -> list[str]:
    """
    Cut a transcript into overlapping parts of roughly `words_per_part` words.

    The parts overlap slightly so a sentence that straddles a boundary is not
    lost to both sides.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= words_per_part:
        return [text]

    step = max(words_per_part - overlap, 1)
    parts = []
    for start in range(0, len(words), step):
        piece = words[start:start + words_per_part]
        if not piece:
            break
        parts.append(" ".join(piece))
        if start + words_per_part >= len(words):
            break
    return parts


if __name__ == "__main__":
    import sys
    import links

    if len(sys.argv) > 1:
        target_url = sys.argv[1]
        target_title = "(given on the command line)"
    else:
        videos, _ = links.get_youtube_links()
        if not videos:
            print("No YouTube links found. Run links.py first to check.")
            raise SystemExit(1)
        target_url = videos[0]["url"]
        target_title = videos[0]["title"]

    print(f"Video : {target_title}")
    print(f"Link  : {target_url}")
    print()

    try:
        result = fetch_transcript(target_url)
    except TranscriptUnavailable as problem:
        print(f"NO CAPTIONS: {problem}")
        raise SystemExit(0)

    kind = "auto-generated by YouTube" if result["generated"] else "written by the creator"
    print(f"Captions found: {result['segments']} lines, language "
          f"'{result['language']}', {kind}")
    print(f"Total length  : {len(result['text']):,} characters, "
          f"about {len(result['text'].split()):,} words")
    print()
    print("First part of the transcript:")
    print("-" * 70)
    print(result["text"][:1200])
    print("-" * 70)
