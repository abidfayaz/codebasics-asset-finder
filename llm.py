"""
The "why did this match?" helper - the only part that talks to an AI service
over the internet.

It sends the search you typed plus the matching text, and asks for one short
sentence explaining the connection. Everything else in this app runs on your
own machine.

If no API key is set up, this file quietly falls back to a simple explanation
built from the overlapping words. The app keeps working either way - it just
gives a plainer reason.

Provider: anything OpenAI-compatible. Groq by default. To switch provider you
only change LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in your .env.
"""

import json

import requests

import config

# How long to wait for the AI service before giving up and using the simple
# explanation instead. Keeps a slow network from freezing the search.
REQUEST_TIMEOUT_SECONDS = 20

# If the last AI call failed, the plain-language reason is stored here so the
# app can show it on screen instead of failing quietly.
last_error: str | None = None

# The rules we give the model. Fixed, so every explanation behaves the same.
SYSTEM_PROMPT = (
    "You explain why a search result matched someone's search. "
    "For each result, write ONE complete sentence of 10 to 20 words, in plain "
    "language a non-technical person understands. Always a full sentence, "
    "never a fragment, and always ending in a full stop. "
    "Say what the slide or page is actually about and how that relates to the "
    "search. "
    "Only describe what is in the provided text - never invent details, and "
    "never guess what else the file might contain. "
    "If the text has little to do with the search, say so honestly. "
    "Good example: 'This slide shows a star schema diagram with fact and "
    "dimension tables.' "
    "Bad example: 'Mentions star schema directly.'"
)

# A different job when NOTHING matched well and we are showing the closest few.
# Here the person has already been told nothing matched, so repeating "this is
# unrelated" three times tells them nothing they do not know. What actually
# helps is knowing what each result IS about, and where the nearest connection
# to their search lies - so they can judge whether it is worth a look.
CLOSEST_SYSTEM_PROMPT = (
    "Nothing in the library matched this person's search well, and they have "
    "already been told that plainly. Your job is to help them judge each of the "
    "closest results. "
    "For each one, write ONE complete sentence of 10 to 20 words saying what "
    "the passage is actually about, and where there is one, the nearest "
    "connection to what they searched for. "
    "Do NOT say it is unrelated, does not mention, or does not relate to the "
    "search - they know, and it wastes the sentence. Stay useful and neutral. "
    "Only describe what is in the provided text - never invent details. "
    "Good example: 'Covers deploying a model to Google Cloud, the nearest thing "
    "here to a deployment pipeline.' "
    "Bad example: 'This text does not relate to Kubernetes or Docker.'"
)


def is_configured() -> bool:
    """True if an API key has been set up, so we can call the AI service."""
    return bool(config.LLM_API_KEY.strip())


def _call_llm(user_prompt: str, system_prompt: str = None) -> str:
    """
    Send one request to the AI service and return its text reply.
    Raises an ordinary exception if anything goes wrong - the caller decides
    what to do about it.
    """
    url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        # Low temperature = steady, repeatable answers.
        "temperature": 0.2,
        # Groq's current models "think" before answering, and that thinking
        # counts against this limit. Leave plenty of room so the answer itself
        # is never cut off.
        "max_tokens": 2000,
        # Ask for a machine-readable reply so we can split it reliably.
        "response_format": {"type": "json_object"},
    }
    # Short explanations need little thinking. Each model family takes its own
    # value: gpt-oss accepts low/medium/high, Qwen accepts "none".
    model = config.LLM_MODEL.lower()
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    elif "qwen" in model:
        body["reasoning_effort"] = "none"

    response = requests.post(url, headers=headers, json=body,
                             timeout=REQUEST_TIMEOUT_SECONDS)

    # Some models refuse the whole request because of the "reply in JSON" or
    # "thinking" switches. The instructions already ask for JSON in plain
    # words, so try once more without them.
    if response.status_code == 400:
        body.pop("response_format", None)
        body.pop("reasoning_effort", None)
        response = requests.post(url, headers=headers, json=body,
                                 timeout=REQUEST_TIMEOUT_SECONDS)

    if not response.ok:
        # Keep the service's own explanation - "400 Bad Request" alone tells
        # nobody what to fix.
        raise requests.HTTPError(
            f"{response.status_code}: {_service_message(response)}",
            response=response)
    return response.json()["choices"][0]["message"]["content"] or ""


def _service_message(response) -> str:
    """The AI service's own description of what went wrong, kept short."""
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = ""
    return (message or response.text or "no details given")[:200]


def _parse_reasons(raw: str) -> list:
    """
    Pull the list of reasons out of the model's reply. Some models wrap the
    JSON in extra words or a ```json block, which is not a real failure - so
    look for the JSON object inside the reply before giving up on it.
    """
    text = (raw or "").strip()
    try:
        return json.loads(text).get("reasons", [])
    except (json.JSONDecodeError, AttributeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1]).get("reasons", [])
        except (json.JSONDecodeError, AttributeError):
            pass
    raise ValueError("the AI reply was not in the expected format")


def simple_reason(query: str, text: str, weak: bool = False) -> str:
    """
    The no-AI-needed explanation: which of your search words appear in the
    text. Used when there is no API key, or the service is unavailable.

    `weak` is for the "closest few" case, where saying what is missing is not
    useful - the person has already been told nothing matched well.
    """
    from search import matching_terms  # imported here to avoid a circular import

    found = matching_terms(query, text)
    if found:
        words = ", ".join(f'"{w}"' for w in found)
        return f"This text contains {words} from your search."
    if weak:
        return "One of the nearest things in your library, by overall meaning."
    return "Close in meaning to your search, though it uses different words."


def why_it_matched(query: str, results: list[dict], weak: bool = False) -> list[str]:
    """
    Produce one explanation per result, in the same order as `results`.

    All results are explained in a SINGLE request rather than one request each,
    which keeps the search fast and the cost low.

    This never raises. If anything at all goes wrong it returns the simple
    word-overlap explanations instead.
    """
    global last_error
    last_error = None

    if not results:
        return []

    # No key configured - use the simple explanation, no network call.
    if not is_configured():
        return [simple_reason(query, r["text"], weak) for r in results]

    # Build one numbered list of the matches for the model to work through.
    blocks = []
    for number, result in enumerate(results, start=1):
        where = result.get("location_label") or ""
        blocks.append(
            f"RESULT {number}\n"
            f"File: {result['file_name']} {where}\n"
            f"Text: {result['snippet']}"
        )

    user_prompt = (
        f'The person searched for: "{query}"\n\n'
        + "\n\n".join(blocks)
        + f"\n\nReply with JSON in exactly this shape, one entry per result, "
        f"in order:\n"
        f'{{"reasons": ["reason for result 1", "reason for result 2", ...]}}\n'
        f"There must be exactly {len(results)} reasons."
    )

    try:
        raw = _call_llm(user_prompt,
                        CLOSEST_SYSTEM_PROMPT if weak else SYSTEM_PROMPT)
        reasons = _parse_reasons(raw)

        # Make sure we got a usable list of the right length. If the model
        # returned too few, pad with the simple explanation.
        cleaned = []
        for index, result in enumerate(results):
            if index < len(reasons) and isinstance(reasons[index], str) and reasons[index].strip():
                cleaned.append(reasons[index].strip())
            else:
                cleaned.append(simple_reason(query, result["text"], weak))
        return cleaned

    except Exception as error:
        # Network down, bad key, rate limit, odd reply - none of these should
        # break the search. Fall back, but record why so the app can say so.
        last_error = _friendly_error(error)
        print(f"Could not get AI explanations ({error}). Using simple reasons.")
        return [simple_reason(query, r["text"], weak) for r in results]


def _friendly_error(error: Exception) -> str:
    """Turn a technical error into something worth showing on screen."""
    text = str(error)
    if isinstance(error, requests.exceptions.Timeout):
        return "the AI service did not reply in time"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "could not reach the AI service - check your internet connection"
    if "401" in text or "invalid_api_key" in text:
        return "the API key was rejected - check LLM_API_KEY in your .env"
    if "404" in text or "model_not_found" in text or "decommissioned" in text:
        return (f"the model '{config.LLM_MODEL}' was not found - check "
                f"LLM_MODEL in your .env against Groq's current model list")
    if "429" in text:
        return "hit the free-tier rate limit - wait a moment and search again"
    return text
