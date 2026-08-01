# Codebasics Asset Finder — Build Spec (for Claude Code)

> Technical spec for an AI coding agent. Not a business doc. Build exactly this scope. Do not add features beyond the "In scope" list without asking. Prefer the simplest thing that works — this is a prototype, not production.

---

## 1. What we are building

A web app that lets a user search a library of content assets (slide decks, PDFs, images/thumbnails) **by meaning**, not by filename, and see **why** each result matched. It reads the actual content of each file once (indexing), stores searchable vectors, and serves a search UI plus a processing-log UI.

Two screens (tabs):
1. **Search** — the main screen. Plain-language search over indexed content, grouped results, "why it matched", highlight, closest-few fallback.
2. **Processing log** — status of indexing: how many files processed, in progress, failed (with reason), and a per-day processing feed so the user can confirm a specific file was indexed.

**Non-developer owner.** Keep dependencies minimal, keep the run steps short, comment code clearly, and fail loudly with readable error messages. Avoid clever abstractions.

---

## 2. Tech stack (use these unless there is a strong reason not to)

- **Language:** Python 3.11+
- **Backend + UI:** Streamlit (single app, easiest for a non-dev to run and deploy; both screens are tabs in one Streamlit app)
- **Vector store:** ChromaDB, local persistent mode (writes to a local folder; no external DB, no server to manage)
- **Embeddings:** sentence-transformers running **locally** (model: `all-MiniLM-L6-v2` or similar small model). Free, no API key, keeps content local. This also matches the project's privacy stance.
- **LLM (text tasks: query understanding, "why it matched", image description prompt):** call via an **OpenAI-compatible client** pointed at **Groq** (free tier) by default. Put base URL, key, and model name in a single config block / `.env` so switching to a paid provider is a one-line change.
- **Text extraction:**
  - PPTX → `python-pptx` (pull text from every shape/slide) OR `markitdown` if simpler. Capture slide number.
  - PDF → `pypdf` (text PDFs). Capture page number.
  - Images/thumbnails → vision step (see §5).
- **File watching (ongoing indexing):** a simple function that scans the assets folder, compares file modified-times against what is already indexed, and processes only new/changed files. Triggered manually by a button and/or on app start. Do NOT build a background daemon; keep it a callable function.

Keep a `requirements.txt`. Assume the user runs `streamlit run app.py`.

---

## 3. Data

- Source files live in a local folder, default `./sample_assets/` (make the path a config value).
- **Real asset types are ONLY these three: PPTX decks, PDFs, and images (PNG/JPG).** If any design mockup shows Word (.docx), Excel (.xlsx/"Sheet"), or zip files, ignore them — those are mockup placeholders, not real scope. Do not build support for them.
- **Confidence label:** show a simple word label per result — "Strong match" or "Close match" — based on the similarity score crossing a threshold. Do NOT show a precise percentage; the word label is more honest and simpler.
- Expected types in the sample: PPTX decks, PDFs, PNG/JPG images and thumbnails.
- **The dataset may be extended.** In particular, a manually-modified deck will be added to demonstrate the multi-term "three-group split" (see §6.3). Document any added files in the README.
- There is **no pre-existing catalog**. The indexer builds it.

---

## 4. Indexing pipeline (the "reader")

A router that processes each file by type. Runs as a callable function `index_assets(folder)` and is also what the Processing-log screen drives.

For every file:
1. Detect type by extension.
2. Route to the right handler:
   - **PPTX handler:** extract text per slide → one indexed **chunk per slide** (so a single slide can be found). Record: file name, path, slide number, extracted text.
   - **PDF (text) handler:** extract text per page → one chunk per page. Record page number.
   - **Image handler:** send the image to the vision model with a fixed instruction (see §5) → get a description + any on-screen text → one chunk. Record: file name, path.
   - **Unknown/broken:** do not crash. Record a **failure** with the filename and a plain reason. Continue.
3. For each chunk: create an embedding (local sentence-transformers) and store in ChromaDB with metadata (file name, path, type, slide/page number, the raw text/description, modified-time, indexed-time).
4. Track per-file status: `processed`, `in_progress`, `failed` (+reason), with timestamps. Persist this status (a small JSON file or a Chroma collection) so the Processing-log screen can read it.

**Two-pass / cost note (implement the simple version):** for PPTX, extract text first (free). Only send *images inside the deck* to the vision model if you can easily extract embedded images; if that is fiddly in the prototype, it is acceptable to treat standalone image files as the image case and skip in-deck image extraction — but leave a TODO comment. Do not block the build on this.

---

## 5. Vision step (images)

- Use a vision-capable model via the same OpenAI-compatible config. **If Groq does not offer a vision model, make the vision provider a separate config value** so the user can point just the vision step at a cheap vision API while keeping text on Groq. Default to whatever Groq offers; if none, stub it clearly.
- Fixed instruction to the vision model (do not let it vary per file):
  > "Describe what this image shows in 1-2 sentences. Then list any text that appears in the image exactly. If the image is a plain logo, icon, or decorative element with no meaningful content, reply only with: DECORATIVE."
- If the reply is `DECORATIVE`, skip embedding that image (cost/noise saving) but still record the file as processed with a note.

---

## 6. Search screen (Tab 1)

### 6.1 Basic search
- A text input + Search button.
- On search: embed the query locally, query ChromaDB for top matches, return ranked results.
- Each result card shows: file name, type, slide/page number if any, a snippet of the matching text/description, and an **Open** action. "Open" should open the **containing folder** of the file (so the user sees it in place), and always show the full local path with a copy-path option as a reliable fallback. Note: a browser-deployed app may be unable to open a local folder for security reasons — in that case just reveal the path and offer copy-path. Never move or alter the file. Do NOT try to launch the file's own application per type; opening the folder or revealing the path is enough.

### 6.2 "Why it matched" + highlight
- For each result, generate a **one-sentence plain-language reason** it matched the query. Do this cheaply: either a short LLM call (Groq) per top result, or a templated explanation using the matched terms if you want to save calls. Prefer the LLM call for the top ~5 results only.
- **Highlight:** in the shown snippet, visually highlight the words/phrases that overlap with the query (simple keyword highlight in the matched text is fine).

### 6.3 Three-group multi-term split (the signature feature)
- Detect when the query contains **two key terms** (e.g. "star schema AND dax", or just "star schema dax" — split on "and"/whitespace into up to two concepts; keep it simple).
- When two terms are present, group results into three labelled sections:
  - **Only A** (matches term 1, not term 2)
  - **Only B** (matches term 2, not term 1)
  - **Has both** (matches both) — show this group first, highlighted as the best.
- Decide membership by checking whether each term's concept is present in the chunk (semantic match per term, or keyword presence as a simple version). Keep the logic readable.
- This will be demoed with a manually-modified deck containing a slide that has both terms. Make sure the grouping renders clearly.

### 6.4 Closest-few fallback
- If no results clear a reasonable similarity threshold, do NOT show an empty screen. Show "Nothing matched exactly — here are the 3 closest" and list the top 3 regardless.

### 6.6 Visual previews (important for the "don't make me open it" goal)
- For **image/thumbnail results**, show the actual image as a small preview on the card, with matching text highlighted in a caption below.
- For **deck-slide results**, show a small preview of the slide if feasible. Rendering a real slide to an image can be hard; an acceptable prototype version is a clean slide-shaped frame containing the slide's extracted text. Leave a TODO to upgrade to a real rendered thumbnail later. Do not block the build on real slide rendering.

### 6.7 Content-type filter (DEFERRED — optional, build only if asked)
- A later, optional enhancement: let the user filter results by type (Deck / PDF / Image) *after* a search. Do NOT build this in the core stages. It is captured here only so it is not forgotten.

---

## 7. Processing-log screen (Tab 2)

Reads the per-file status recorded during indexing. Shows:

- **Summary counts:** total files, processed, in progress, failed.
- **A "Run indexing" button** that calls `index_assets()` and updates status live (or on rerun).
- **A table of files** with columns: file name, type, status, slide/page count indexed, processed-time, and **failure reason** where failed.
- **Failed files section:** clearly list any failures with the plain-language reason (e.g. "could not read PDF — file may be scanned", "unsupported file type").
- **Per-day feed:** a simple reverse-chronological list grouped by date — "12 files processed on 2026-07-27", expandable to the filenames. This lets the user confirm a specific recent file was indexed.
- This screen is a real trust feature: the user should be able to answer "was my latest file indexed?" at a glance.

---

## 8. In scope / out of scope (do not build beyond this)

**In scope:** everything in §4–§7.

**Out of scope for the prototype (do NOT build):**
- Moving, renaming, de-duplicating, or reorganising files.
- Copying file content out of the app (only reveal the path / link back).
- Reading LinkedIn or gated content (LinkedIn links: skip entirely).
- Website page content (messy: clutter, blocked pages, images) — possible later extension, not in the prototype.
- Reading on-screen visuals inside videos. **YouTube is handled via captions/transcripts only (spoken words), see R3.** Videos without usable captions are flagged, not failed.
- Auth, user accounts, multi-user.
- A background/always-on watcher daemon (indexing is button-triggered).
- Any cloud database or hosted vector service.

---

## 9. Guardrails / behaviour

- **Never modify or delete source files.** Read-only access to the assets folder. This is a hard rule.
- **Never fabricate results.** Every result maps to a real indexed chunk from a real file.
- **Weak matches labelled as weak** (the closest-few state), not shown as confident.
- **Failures are surfaced, never silent** — a file that cannot be read appears in the Processing-log with a reason.
- **Config for all secrets/models** in `.env` (never hardcode keys). Provide `.env.example`.

---

## 10. Config block (single source of truth)

Put near the top of the app or in `config.py` / `.env`:

```
ASSETS_FOLDER=./sample_assets
CHROMA_DIR=./chroma_store
STATUS_FILE=./index_status.json

# text LLM (Groq by default, OpenAI-compatible)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=...            # from .env
LLM_MODEL=llama-3.3-70b-versatile   # or current Groq text model

# vision (may be a different provider if Groq has no vision model)
VISION_BASE_URL=...
VISION_API_KEY=...
VISION_MODEL=...

# search
SIMILARITY_THRESHOLD=0.35   # tune; below this -> closest-few fallback
TOP_K=10
```

Switching providers later = change these values only.

---

## 11. Deliverables

- `app.py` (Streamlit, both tabs) — or a small module split if cleaner (`indexer.py`, `search.py`, `app.py`).
- `requirements.txt`
- `.env.example`
- `README.md`: what it is, how to install, how to run (`streamlit run app.py`), how to add files, what was added to the sample dataset (note the modified deck for the three-group demo), and the known prototype/production gaps.

---

## 12. Build order (see the separate staged checklist)

Do not build everything at once. Follow the staged checklist: skeleton → indexer (text) → search basic → why/highlight → three-group → closest-few → vision → processing-log → polish/deploy. Each stage should run and be testable before the next.
