"""
Config - the single place where every setting lives.

Settings are looked for in this order:
  1. Streamlit's secrets, when the app is deployed to Streamlit Community
     Cloud. You paste them into the Secrets box there - see the README.
  2. A file called ".env" on your own computer, for local use. Never shared.
  3. The sensible default written here.

Because secrets are checked first, the same code runs locally and on the
server with no changes: locally there are no Streamlit secrets, so it falls
through to your .env.

To change a setting on your own machine, edit ".env" - not this file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The folder this file sits in. Everything else is measured from here, so the
# app works no matter which directory you launch it from.
PROJECT_ROOT = Path(__file__).parent.resolve()

# Read .env into the environment (does nothing if the file is absent).
load_dotenv(PROJECT_ROOT / ".env")


def _from_streamlit_secrets(name: str):
    """
    Look the setting up in Streamlit's secrets, if we are running inside a
    deployed Streamlit app.

    Everything here is defensive on purpose: `indexer.py` and the other tools
    run as plain Python scripts with no Streamlit around them, and reading
    secrets in that situation must not raise or print warnings.
    """
    try:
        import streamlit as st
    except Exception:
        return None

    try:
        if name in st.secrets:
            value = st.secrets[name]
            return str(value) if value is not None else None
    except Exception:
        # No secrets file, or not running under Streamlit at all.
        return None
    return None


def setting(name: str, default: str = "") -> str:
    """
    Read one setting: Streamlit secrets first, then .env, then the default.
    """
    from_secrets = _from_streamlit_secrets(name)
    if from_secrets is not None and str(from_secrets).strip() != "":
        return str(from_secrets)
    return os.getenv(name, default)


def _path_setting(name: str, default: str) -> Path:
    """Read a folder/file setting and turn it into a full, absolute path."""
    raw = setting(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _float_setting(name: str, default: float) -> float:
    """Read a decimal-number setting, with a clear error if it is not a number."""
    raw = setting(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"Setting {name} in your .env should be a number, but it is '{raw}'."
        )


def _int_setting(name: str, default: int) -> int:
    """Read a whole-number setting, with a clear error if it is not a number."""
    raw = setting(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Setting {name} in your .env should be a whole number, but it is '{raw}'."
        )


# ---------- Folders ----------
# Source files. The app only ever READS from here - never writes or deletes.
ASSETS_FOLDER = _path_setting("ASSETS_FOLDER", "./sample_assets")
# The searchable index (created automatically the first time we index).
CHROMA_DIR = _path_setting("CHROMA_DIR", "./chroma_store")
# Small record of which files were indexed, and which failed and why.
STATUS_FILE = _path_setting("STATUS_FILE", "./index_status.json")

# ---------- Text LLM (Groq by default, OpenAI-compatible) ----------
LLM_BASE_URL = setting("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = setting("LLM_API_KEY", "")
LLM_MODEL = setting("LLM_MODEL", "llama-3.3-70b-versatile")

# ---------- Vision model (reads images) ----------
# Kept as separate settings so the image step can be pointed at a different
# provider from the text step, without touching anything else.
# Left blank, they fall back to the text provider above - which works, because
# Groq does offer a vision-capable model.
VISION_BASE_URL = setting("VISION_BASE_URL", "") or LLM_BASE_URL
VISION_API_KEY = setting("VISION_API_KEY", "") or LLM_API_KEY
VISION_MODEL = setting("VISION_MODEL", "") or "qwen/qwen3.6-27b"

# ---------- Embeddings (runs locally, no API key needed) ----------
EMBEDDING_MODEL = setting("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# ---------- Search tuning ----------
# 0.50 is the tuned value and the one the app is documented against. It is the
# DEFAULT, not just something set in .env, so a deployment that only supplies
# an API key still behaves the same as it does locally.
SIMILARITY_THRESHOLD = _float_setting("SIMILARITY_THRESHOLD", 0.50)
TOP_K = _int_setting("TOP_K", 10)
# How many of the top results get an AI-written "why it matched" sentence.
# Kept small on purpose: each extra result costs time and money.
WHY_TOP_N = _int_setting("WHY_TOP_N", 5)
# When nothing clears the threshold, how many "closest anyway" results to show
# instead of leaving the person at an empty screen.
CLOSEST_FEW = _int_setting("CLOSEST_FEW", 3)

# The only file types this app handles. Anything else is reported as
# "unsupported" in the processing log rather than silently ignored.
SUPPORTED_EXTENSIONS = {".pptx", ".pdf", ".png", ".jpg", ".jpeg"}

# ---------- Deployment ----------
# Set READ_ONLY=true in the Streamlit secrets when the app is published.
# The published app ships with a ready-made index and no source decks, so
# indexing there would find nothing to read and would spend API credit for
# no reason. Read-only hides the "Run indexing" button and explains why.
READ_ONLY = setting("READ_ONLY", "false").strip().lower() in ("1", "true", "yes")
