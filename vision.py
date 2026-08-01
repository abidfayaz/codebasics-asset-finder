"""
The image reader.

Images have no text to extract, so we show each one to a vision model and ask
it what it sees. The answer - a short description plus any text printed in the
image - is what gets indexed, which is how a thumbnail becomes findable by the
words printed on it.

Provider: whatever VISION_* points at in your .env. Left blank, it uses the
same Groq account as the text model, because Groq offers a vision model.
"""

import base64
import io
import re
import time

import requests

import config

# The exact wording from BUILD_SPEC section 5. Fixed on purpose - every image
# is asked the same thing, so results stay consistent.
VISION_INSTRUCTION = (
    "Describe what this image shows in 1-2 sentences. Then list any text that "
    "appears in the image exactly. If the image is a plain logo, icon, or "
    "decorative element with no meaningful content, reply only with: DECORATIVE."
)

# Big photos are shrunk before sending. A 4000-pixel photo costs a lot more to
# send and tells the model nothing extra - text stays readable at this size.
MAX_IMAGE_SIDE = 1024

REQUEST_TIMEOUT_SECONDS = 30

# The free Groq tier allows a limited number of tokens per minute. Each image
# costs roughly 1,000-1,500 of them, so we pause between images and wait it out
# if we are told to slow down.
PAUSE_BETWEEN_IMAGES_SECONDS = 12
RATE_LIMIT_WAIT_SECONDS = 15
# Deliberately few. When the free allowance is used up, waiting longer does not
# help - it just leaves you staring at a program that looks frozen. Better to
# fail in seconds and say so, then try again later.
MAX_ATTEMPTS = 2
# Never wait longer than this for one picture, however long we are told to.
MAX_WAIT_SECONDS = 20


def _suggested_wait(message: str) -> float | None:
    """
    Groq usually says how long to wait, e.g. "Please try again in 6.79s" or
    "try again in 1m30s". Honouring that is far more reliable than guessing.
    """
    match = re.search(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", message,
                      re.IGNORECASE)
    if not match:
        return None
    minutes = float(match.group(1) or 0)
    seconds = float(match.group(2))
    # A second of headroom, and never longer than our own ceiling.
    return min(minutes * 60 + seconds + 1, MAX_WAIT_SECONDS)


class VisionError(Exception):
    """Raised when an image genuinely could not be read."""


# If the service refuses this many pictures in a row, the free allowance is
# almost certainly used up for now. Carrying on would mean minutes of waiting
# per picture for nothing, so we stop asking for the rest of this run and let
# the indexer record them as still to do.
GIVE_UP_AFTER_REFUSALS = 2
_refusals_in_a_row = 0


def reset_give_up_counter() -> None:
    """Start a fresh run with a clean slate."""
    global _refusals_in_a_row
    _refusals_in_a_row = 0


def giving_up() -> bool:
    """True once we have decided to stop asking for this run."""
    return _refusals_in_a_row >= GIVE_UP_AFTER_REFUSALS


def is_configured() -> bool:
    """True if there is an API key available for the image step."""
    return bool(config.VISION_API_KEY.strip())


def _prepare_image(source) -> tuple[str, str]:
    """
    Open the image, shrink it if it is large, and turn it into the text form
    the API expects. Returns (base64 text, mime type).

    `source` is either a path to a file on disk, or the raw bytes of an image
    that was pulled out of a PowerPoint slide.
    """
    from PIL import Image

    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source

    with Image.open(handle) as image:
        # Photos from phones/cameras can carry odd colour modes; JPEG needs RGB.
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        if max(image.size) > MAX_IMAGE_SIDE:
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=85)

    return base64.b64encode(buffer.getvalue()).decode(), "image/jpeg"


def describe_image(source) -> str:
    """
    Ask the vision model what this image shows.

    `source` is a path to an image file, or the raw bytes of a picture taken
    from inside a slide - both are handled the same way.

    Returns the model's answer as plain text. Raises VisionError if the image
    could not be read at all, so the indexer can record a proper failure.
    """
    global _refusals_in_a_row

    if not is_configured():
        raise VisionError(
            "no vision API key - set VISION_API_KEY (or LLM_API_KEY) in .env"
        )

    if giving_up():
        raise VisionError(
            "the free allowance looks used up - skipping the remaining "
            "pictures this run, run indexing again later to finish them"
        )

    try:
        encoded, mime = _prepare_image(source)
    except Exception as error:
        raise VisionError(f"could not open the image file ({error})")

    body = {
        "model": config.VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_INSTRUCTION},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }],
        "temperature": 0.2,
        "max_tokens": 400,
        # This model thinks out loud by default, and that thinking eats the
        # whole reply budget - leaving an empty answer. Switching it off gives
        # us just the description, using about a fifth of the tokens.
        "reasoning_effort": "none",
    }

    last_problem = "unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                f"{config.VISION_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.VISION_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 200:
                _refusals_in_a_row = 0        # working again
                return response.json()["choices"][0]["message"]["content"].strip()

            message = response.json().get("error", {}).get("message", "")

            # Too fast for the free tier, or the service is briefly swamped.
            # Both clear up on their own, so wait and try again.
            busy = "over capacity" in message.lower()
            if response.status_code in (429, 500, 502, 503) or busy:
                last_problem = ("the vision service was busy" if busy
                                else "hit the free-tier rate limit")
                # Wait exactly as long as we are told to, when we are told.
                # Otherwise back off a little further on each attempt.
                wait = _suggested_wait(message)
                if wait is None:
                    wait = min(RATE_LIMIT_WAIT_SECONDS * attempt,
                               MAX_WAIT_SECONDS)
                print(f"      {last_problem}, waiting {wait:.0f}s "
                      f"(attempt {attempt} of {MAX_ATTEMPTS})")
                time.sleep(wait)
                continue

            raise VisionError(f"vision service said: {message[:160]}")

        except requests.exceptions.Timeout:
            last_problem = "the vision service did not reply in time"
        except requests.exceptions.ConnectionError:
            last_problem = "could not reach the vision service"
        except VisionError:
            raise
        except Exception as error:
            last_problem = str(error)

        if attempt < MAX_ATTEMPTS:
            time.sleep(5)

    _refusals_in_a_row += 1
    raise VisionError(f"{last_problem} after {MAX_ATTEMPTS} attempts")


def is_decorative(description: str) -> bool:
    """
    Did the model say this image is just a logo or decoration?

    Those get recorded as processed but are not indexed - they would only add
    noise to search results.
    """
    cleaned = (description or "").strip().strip("*.\" ").upper()
    return cleaned.startswith("DECORATIVE")
