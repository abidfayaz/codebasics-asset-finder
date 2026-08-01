# Codebasics Asset Finder

Search a library of slide decks, PDFs, images and YouTube videos **by meaning**,
not by filename — and see *why* each result matched.

Ask for "how do I write a data analyst resume" and it finds the right PDF, even
though none of those words are in the filename. Ask for "DAX Fundamentals" and it
finds the thumbnail with those words printed inside the picture. Ask "what is a
pandas dataframe" and it finds the minute of the video where that is explained.

---

## What it does

The app has two screens.

### Search

Type a plain-language question and you get **one ranked list, best matches
first** — however many words you searched for. Each result card shows:

- the file name (or video title), the type, and the slide / page / part number
- a **confidence label**: "Strong match" or "Close match". A word, not a
  fake-precise percentage. A result counts as strong if its similarity clears
  `SIMILARITY_THRESHOLD` (0.50) **or** every word you typed appears literally in
  the text. That second test matters: similarity compares your whole search
  against a whole passage, so searching a bare name like "sam altman" scores low
  against a long paragraph that plainly names him. Short, exact searches are how
  people really search, and judging them on overall similarity alone gets them
  wrong
- a **"why it matched"** sentence. The top 5 get one written by AI (blue **AI**
  badge); the rest get a free word-overlap explanation (grey **word check**
  badge), so no card is ever left blank. When a result is genuinely weak the
  sentence says so rather than inventing a connection. In the "3 closest"
  fallback the heading changes to **"what this covers"** and the sentences
  describe what each result *is* about — repeating "this does not match" under a
  banner that already says nothing matched would be useless
- **every one of your search words highlighted** in the text. Everyday words
  like "the" and "how" are ignored so they do not light up everything
- a **preview**: the actual picture for an image, a slide-shaped frame for a
  deck slide, the quoted passage for a video
- a way to get to it: the full file path with a copy button, or a
  **"Watch on YouTube"** link for videos

**No dead ends.** If nothing clears the quality threshold you get
*"Nothing matched exactly — here are the 3 closest"* rather than an empty screen.

**Recent searches** appear as clickable chips above the box. They last as long as
the browser tab is open.

### Processing log

Proof of what the app has read, and the place to answer "was my latest file
picked up?".

- **Summary counts** — files seen, processed, in progress, failed, skipped
- **A "Run indexing" button** that reads any new or changed content
- **A table of everything**, listing type, status, how many searchable pieces
  came out of it, when it was processed, and any reason. **Sorted newest first**,
  so whatever you added most recently is at the top. Items never actually read
  (skipped types) have no time and sit at the bottom
- **Failed** and **Skipped** as separate sections. Failed means something is
  wrong with the file; skipped means a type this app does not handle
- **A per-day history** — "N files processed on DATE", expandable to the
  filenames and the minute each was read

Videos appear here too, alongside files: a video whose captions could not be
fetched shows as failed with the reason, so nothing goes missing quietly.

---

## Installing it

You need **Python 3.11 or newer** (built and tested on 3.14).

Open a terminal in this folder and run these once:

```bash
python -m venv .venv
```

```bash
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

That last step downloads a few hundred megabytes (it includes PyTorch) and takes
a few minutes.

### Add your API key

The app needs a free Groq account for two things: writing the "why it matched"
sentences, and describing images.

1. Get a free key at [console.groq.com](https://console.groq.com) → API Keys.
2. Copy `.env.example` to a new file called `.env`.
3. Open `.env` in Notepad and paste your key after `LLM_API_KEY=`
   (no quotes, no spaces).

`.env` is never shared or committed — it is listed in `.gitignore`.

**Without a key the app still runs.** Search works completely (it runs on your own
machine), and the "why it matched" text falls back to word-overlap explanations.
Only the image-reading step needs the key. YouTube transcripts need no key at all.

---

## Running it

**Step 1 — read your content** (once, and again whenever you add files):

```bash
.\.venv\Scripts\python.exe indexer.py
```

**Step 2 — start the app:**

```bash
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Then open <http://localhost:8501>. Press `Ctrl+C` in the terminal to stop it.

The **first search takes about 15 seconds** while the language model loads. Every
search after that is instant. That is normal, not a hang.

### Doing one job at a time

Reading pictures and fetching video captions both depend on outside services with
free-tier limits, and both can be slow. They can be run on their own, in small
batches, so neither ever blocks the other:

```bash
.\.venv\Scripts\python.exe indexer.py --videos-only
```

```bash
.\.venv\Scripts\python.exe indexer.py --pictures-only --limit 5
```

- `--videos-only` — YouTube transcripts only. No files touched, no images sent
  anywhere.
- `--pictures-only --limit N` — work through outstanding slide pictures, N new
  pictures at a time.
- `--force` — redo work already done (rarely needed).

---

## Adding your own files

1. Copy files into the assets folder (`sample_assets` by default, or wherever
   `ASSETS_FOLDER` in your `.env` points). Sub-folders are fine.
2. Either press **Run indexing** on the Processing log tab, or run
   `python indexer.py` in a terminal.
3. Check the Processing log to confirm the file shows as ✅ Processed.

**It only reads what is new.** Every file's last-modified time is recorded when it
is indexed; unchanged files are skipped entirely on the next run — not re-read,
not re-embedded, and their pictures are not sent to the vision model again.
Files that *failed* are retried automatically.

**Deleted and renamed files are cleaned up.** At the start of each run, any entry
whose file is no longer on disk is removed from both the search index and the
Processing log, so results never point at a file that has gone. As a safety net,
if *every* known file looks missing — an unplugged drive, or OneDrive mid-sync —
nothing is removed and the run says why.

**Renaming costs nothing.** Picture descriptions are remembered against the
picture's own content, not its filename, in a shared library. Rename a deck, or
keep two copies of one, and its pictures are recognised instantly rather than
being read again.

### Supported content

| Type | What gets indexed |
|---|---|
| `.pptx` decks | The typed text of each slide, **plus a description of every picture on that slide** — so a slide holding nothing but a screenshot is still findable |
| `.pdf` files | One searchable piece per page, with the page number |
| `.png` `.jpg` images | An AI description of the picture, plus any text printed in it |
| **YouTube videos** | The spoken words, from the video's public captions (see below) |

Decks are read in two passes to keep costs down. The typed text is read first and
is free. Only then are the slide's pictures sent to the vision model, and even
then the app skips tiny pictures (icons, dividers) and any picture it has already
described. Pictures the model reports as purely decorative are dropped rather
than indexed.

When a result comes from a picture rather than typed text, the card says so:
*"SLIDE 3 · FROM A PICTURE ON THIS SLIDE"*.

Anything else (`.zip`, `.docx`…) is **skipped**, not failed — nothing is wrong
with the file, the app simply does not index that type as a searchable document.
`public_links.xlsx` also appears as skipped, with the reason *"link list — read
separately to find the YouTube videos"*: it is very much used, just not indexed
as a document. **Failed** is reserved for files that are genuinely broken, and
they appear in the Processing log with a plain-English reason.

**Never modified.** The app only ever reads your content. It does not move,
rename, edit or delete anything.

---

## YouTube videos

Some of the library is not files at all — it is videos on YouTube. The app makes
those searchable **by what is said in them**.

**How it works:**

1. It looks for a spreadsheet called **`public_links.xlsx`** in the assets folder,
   with three columns: *Title / Description*, *url*, *source_type*.
2. It keeps only the rows where `source_type` is **youtube**. LinkedIn rows are
   skipped (they sit behind a login) and website rows are skipped (web pages are
   messy) — both are counted and reported, never silently dropped.
3. For each video it fetches the **public captions** — the same subtitles you can
   turn on in your browser — using `youtube-transcript-api`. **Free, no API key,
   no account.**
4. The transcript is cut into overlapping **~180-word excerpts**, so a search
   points at *the stretch of the video* where a subject comes up rather than the
   whole thing. A long tutorial becomes dozens of separately findable excerpts —
   the 21,000-word roundtable becomes 141 of them.

Results show these as **"excerpt 4 of 13"**. That numbering is **ours**, not the
creator's: it counts our slices of the transcript and has nothing to do with how
a channel numbers its own episodes. The total is always shown so it reads as a
position within one video, and roughly how far through it the match sits.

The current spreadsheet lists **59 links: 32 YouTube, 21 LinkedIn, 6 website.**
Two of those YouTube rows are the same video listed twice — once as a
`youtube.com/watch?v=…` link and once as a short `youtu.be/…` share link — so
there are **30 distinct videos**. Videos are recognised by their YouTube id, not
their web address, so a video listed twice is only fetched and indexed once.

**A video result** shows the video title, a **"YouTube video"** tag with the part
number, the matching passage in quotes under *"SPOKEN IN THIS VIDEO"*, a
"why it matched" reason, and a **"Watch on YouTube"** link that opens the video in
a new tab. There is no file path, because a video is not a file.

### Honest caveats

- **Captions are spoken words only.** If a video shows a chart or a slide on
  screen without describing it aloud, that content is not searchable. The app
  does not watch the video.
- **Not every video has usable captions.** Some owners disable them; some videos
  have none in English. Those are recorded in the Processing log as
  **skipped, with the reason** — never silently missing.
- **Public videos only.** Private, unlisted and age-restricted videos cannot be
  read.
- **YouTube rate-limits heavy use.** `youtube-transcript-api` is not an official
  API, so fetching many transcripts quickly can get your connection temporarily
  refused — it happened while indexing this dataset, at around 17 videos in a few
  minutes. When it does, the run stops after three refusals and marks the
  remaining videos as *still to do*; a later run picks them up automatically and
  nothing is lost. Fetching in batches of about five avoids it.

---

## The sample dataset

`sample_assets` currently contains **29 files**:

- **7 decks** — `DA vs DS vs DE.pptx`, `is_the_ai_bubble_about_to_burst.pptx`,
  `linear_regression_with_single_variable.pptx`, `star_schema.pptx`, `svm.pptx`,
  `what_is_ML.pptx`, `what_is_bi_tool.pptx`
- **3 PDFs** — bootcamp brochure, data-analyst resume checklist, GenAI careers
  handout
- **18 images** — course thumbnails, banners, infographics and photos
- **1 spreadsheet** — `public_links.xlsx`, the list of YouTube/LinkedIn/website
  links

**A good deck to demonstrate picture reading:**
`is_the_ai_bubble_about_to_burst.pptx` has 8 slides but typed text on only one of
them. The other slides are news screenshots. Before pictures were read, 7 of its
8 slides were invisible to search. Now searches like *"Sam Altman warning about an
AI bubble"* or *"MIT report generative AI pilots failing"* return the right slide.

**A good search to show everything at once:** *"linear regression explained"*
returns the deck slide, the course thumbnail **and** the moment in the YouTube
tutorial where it is taught — three different kinds of content, one ranked list.

### Searches that show it working

Each of these was checked against the indexed content; the scores are what the
app actually returns today.

| Search | Top result | Why it is worth showing |
|---|---|---|
| `support vector machine` | svm_thumbnail.png — **0.75** | The picture itself is the best match, ahead of the deck about it |
| `Sam Altman warning about an AI bubble` | is_the_ai_bubble… **Slide 1** — **0.70** | That slide has no typed text at all; it is a news screenshot the vision model read |
| `what is power bi` | what_is_bi_tool.pptx **Slide 3** — **0.66** | Answers one plain question with a slide, a video and two thumbnails |
| `star schema` | star_schema.pptx **Slide 3** — **0.65** | The deck slide *and* the video explaining it, back to back |
| `building a power bi sales dashboard` | Sales Insights video, **excerpt 3 of 9** — **0.75** | Lands on the walkthrough inside the project video |
| `what is a pandas dataframe` | Pandas Tutorial 1, **excerpt 1 of 9** — **0.64** | Points at the stretch of a video, not just the video |
| `how do I write a data analyst resume` | Data Analyst Roadmap video, **excerpt 29 of 33** — **0.63**; the resume checklist PDF is second at **0.61** | A question in plain words, answered by both a video moment and a PDF page |
| `DAX Fundamentals` | powerbi_course_thumb_07.png — **0.61** | Nothing in the filename says DAX; the words were read off the image |
| `linear regression explained` | thumbnail, deck slide and video together | All three content types in one ranked list |
| `sam altman` | is_the_ai_bubble… **Slide 1** — **0.34**, still a **Strong match** | A bare name scores low on similarity but every word is present, so it is correctly treated as strong |
| `kubernetes docker deployment pipeline` | best score **0.18** | Nothing in the library covers it, so the "3 closest" fallback appears |

---

## How it works, briefly

| Step | What runs | Where |
|---|---|---|
| Reading decks and PDFs | `python-pptx`, `pypdf` | your machine |
| Reading the link spreadsheet | `openpyxl` | your machine |
| Reading images | Groq vision model (`qwen/qwen3.6-27b`) | Groq's servers |
| Fetching video captions | `youtube-transcript-api` | YouTube (public captions) |
| Turning text into searchable vectors | `sentence-transformers` (`all-MiniLM-L6-v2`) | your machine |
| Storing and searching vectors | ChromaDB | your machine, in `chroma_store/` |
| "Why it matched" sentences | Groq text model (`llama-3.3-70b-versatile`) | Groq's servers |

Only two things leave your computer: image contents during indexing, and short
text snippets when generating explanations. Search itself is entirely local.

### Files in this project

| File | What it is |
|---|---|
| `app.py` | The web app — both screens |
| `indexer.py` | Reads your content and builds the search index |
| `search.py` | Runs searches and highlights your search terms |
| `vision.py` | Sends images to the vision model |
| `llm.py` | Asks the AI why a result matched |
| `links.py` | Reads `public_links.xlsx` and picks out the YouTube rows |
| `transcripts.py` | Fetches and splits video captions |
| `config.py` | Every setting in one place, read from `.env` |
| `.streamlit/config.toml` | Forces the light theme, disables the first-run prompt |

Generated automatically, safe to delete (they rebuild): `chroma_store/`,
`index_status.json`.

### Settings you can change

All live in `.env`:

| Setting | Default | What it does |
|---|---|---|
| `ASSETS_FOLDER` | `./sample_assets` | Where your content lives |
| `SIMILARITY_THRESHOLD` | `0.50` | Below this, a result is a "Close match"; if nothing reaches it, the "3 closest" fallback kicks in |
| `TOP_K` | `10` | How many results per search |
| `WHY_TOP_N` | `5` | How many results get an AI-written reason |
| `CLOSEST_FEW` | `3` | How many to show in the fallback |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Groq's text model |
| `VISION_MODEL` | `qwen/qwen3.6-27b` | Groq's vision model |

Leave the `VISION_*` settings blank to use the same Groq account as the text
model. Fill them in only to put the image step on a different provider — that is
a three-line change and nothing else moves.

**Settings are read once at startup.** After editing `.env`, stop the app
(`Ctrl+C`) and start it again — a browser refresh is not enough.

---

## Prototype versus production

**This is a prototype**, built against a small sample dataset. Indexing is
triggered by a button and finishes in seconds to minutes, because there are only
about 30 files. That is the right design at this size — there is no point running
a scheduler for a folder you can read in one go.

**At full scale (~11 TB of company content) the same indexer would run
differently**, without changing the way it works:

- **Scheduled, not button-triggered.** The same `index_assets()` function would run
  on a schedule rather than on a click.
- **Batched over roughly two weeks.** The initial pass over 11 TB would be split
  into batches spread across about a fortnight, rather than attempted in one run.
- **Batch pricing.** Those batches would use the AI providers' batch rates, which
  are substantially cheaper than the per-request pricing used here. See the
  separate cost estimate for the figures.
- **After the first pass, only changes matter.** The modified-time check already
  built into the indexer means day-to-day running costs are tiny — only new and
  edited files are ever re-read.

**Where the files would come from.** OneDrive would sync the company library down
to a local folder, and the app would watch that folder. Nothing about the indexer
changes — it already reads a local folder recursively and picks up new, changed
and deleted files.

**Keeping content private.** For a private deployment, both AI steps would run on
self-hosted models: the vision model and the embedding model would run on company
hardware, so **no file content would ever leave the company network**. The
embedding step already runs locally today. Only the vision and explanation steps
call out to Groq, and both are isolated behind `vision.py` and `llm.py` with their
own configuration — swapping them for a self-hosted endpoint is a change to
`.env`, not to the application.

---

## Known gaps

Honest list of what this prototype does not do yet.

1. **A video link opens at the start, not at the matching moment.** You are told
   which part of the transcript matched, but the link takes you to 0:00. Jumping
   straight to the passage is a planned improvement: it needs the transcripts
   re-fetched with their timing data, so each part can carry a start time and the
   link can end in `&t=412s`.
2. **Captions are spoken words only** — see the YouTube caveats above. A video
   that shows something without saying it is not searchable.
3. **Real slide thumbnails.** Deck results show a slide-shaped frame containing the
   slide's text, not a picture of the actual slide. Rendering real slides needs
   PowerPoint or LibreOffice installed.
4. **Change detection uses timestamps.** A program that rewrites a file without
   changing its modified time would go unnoticed.
5. **Long slides and pages are only partly represented.** The embedding model reads
   roughly the first 256 words of each piece. Video transcripts are split into
   overlapping parts to avoid this; slides and pages are not.
6. **A slide's pictures are described separately, not together.** A slide whose
   meaning comes from two pictures side by side is understood as two things.
7. **Vision descriptions are the model's words, not yours.** Search matches what
   the model wrote about a picture, so very small print in an image may not match.
8. **Results reveal a path; they do not open the file.** A browser cannot open a
   local folder for security reasons, so file results show the full path with a
   copy button.

### What is indexed today

**792 searchable pieces, with nothing outstanding.**

| Source | Pieces |
|---|---|
| YouTube transcripts | 658 |
| Deck slides — 56 from typed text, **57 from pictures** | 113 |
| Images | 18 |
| PDFs | 3 |

| Content | State |
|---|---|
| Decks and their slide pictures | ✅ complete — all 55 pictures read |
| PDFs and standalone images | ✅ complete |
| YouTube videos | ✅ all 30 indexed — none skipped, none failed |

Worth noting: **more than half the deck content came from pictures** (57 pieces
against 56 of typed text). Before slide pictures were read, roughly half the
slides in these decks could not be found at all.

Both AI services throttle heavy use of their free tiers, and both were hit while
indexing this dataset. Neither needed a code fix — the work resumes where it left
off and is done in small batches:

```bash
.\.venv\Scripts\python.exe indexer.py --pictures-only --limit 5
```

Each run reads only what is missing, reuses everything already described, and
says plainly when an allowance is used up. Nothing is ever read twice.

---

## Troubleshooting

**Changes to the code or `.env` are not showing up.** Stop the app with `Ctrl+C`
and start it again. Streamlit reloads `app.py` automatically but not the other
files, and never re-reads `.env`. This is the single most likely cause of any
strange behaviour after a change.

**"Port 8501 is already in use."** The app is already running in another terminal.
Stop that one first.

**"The free allowance looks used up."** Groq's free vision tier is exhausted for
now. Indexing stops asking after two refusals rather than hanging. Wait a few
hours and run `--pictures-only` again.

**"YouTube is refusing requests from this computer."** YouTube has temporarily
rate-limited your connection. The run stops after three refusals and records the
rest as still to do. Wait a few hours and run `--videos-only` again.

**"Showing simple reasons: …"** appears above results. The Groq text call failed —
the message says why (bad key, rate limit, no internet, retired model). Search
itself is unaffected; only the AI explanations fall back.

**An error about pandas / Application Control.** Some Windows security policies
block a file inside pandas. This app avoids pandas entirely for that reason.
