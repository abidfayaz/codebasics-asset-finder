# Codebasics Asset Finder — Staged Build Checklist

Build in these stages, in order. Each stage is one focused Claude Code session. Each stage must **run and be testable** before starting the next. This keeps token use low and means a break only affects one small piece.

Give Claude Code `BUILD_SPEC.md` at the start of every session. Then paste only the current stage's instruction.

---

## Stage 0 — Project skeleton  (small)
**Goal:** an empty Streamlit app with two tabs that runs.
- Set up folder, `requirements.txt`, `.env.example`, `config.py`.
- `app.py` with two tabs: "Search" and "Processing log" — each just showing a placeholder heading.
- Confirm `streamlit run app.py` opens and both tabs show.
**Test:** app opens, two tabs visible.

---

## Stage 1 — Text indexer  (medium)
**Goal:** read PPTX and PDF text into ChromaDB. No UI yet, a callable function.
- `indexer.py` with `index_assets(folder)`.
- PPTX → one chunk per slide (with slide number). PDF → one chunk per page.
- Local embeddings (sentence-transformers), store in ChromaDB with metadata.
- Record per-file status (processed/failed + reason) to `index_status.json`.
- Handle broken/unknown files without crashing.
**Test:** run the function on `sample_assets/`; print how many chunks indexed and any failures.

---

## Stage 2 — Basic search  (medium)
**Goal:** the Search tab returns real results.
- Search box + button. Embed query, query ChromaDB, show ranked result cards.
- Card shows: file name, type, slide/page, matched snippet, path.
**Test:** search a word you know is in a deck; the right slide comes back.

---

## Stage 3 — Why-it-matched + highlight  (medium)
**Goal:** trust features on each result.
- One-sentence "why it matched" for the top ~5 results (Groq call).
- Highlight query words in the shown snippet.
**Test:** results show a sensible reason and highlighted terms.

---

## Stage 4 — Three-group split  (medium, the signature feature)
**Goal:** two-term queries split into Only A / Only B / Has both.
- Detect two terms (split on "and"/whitespace, max two).
- Group results; show "Has both" first, highlighted.
- Add the manually-modified demo deck to `sample_assets/` and note it in README.
**Test:** search "star schema and dax"; three groups render, the both-slide is in "Has both".

---

## Stage 5 — Closest-few fallback  (small)
**Goal:** no dead ends.
- If nothing beats the similarity threshold, show "Nothing exact — 3 closest" and list top 3.
**Test:** search a nonsense-but-related phrase; get the 3 closest, not an empty screen.

---

## Stage 6 — Recent searches  (small)
**Goal:** flow preservation.
- Keep last few searches in session state; show as clickable chips.
**Test:** do a few searches; chips appear and are clickable.

---

## Stage 7 — Vision step for images + visual previews  (medium)
**Goal:** images/thumbnails become searchable, and results show visual previews.
- Image handler sends image to vision model with the fixed instruction.
- DECORATIVE images skipped from embedding but recorded as processed.
- If Groq has no vision model, wire `VISION_*` config to a cheap vision API; otherwise stub with a clear message.
- Visual previews: image results show the actual image on the card; deck-slide results show a slide-shaped frame with the slide text (real rendered slide thumbnail is a later TODO).
**Test:** search for text on a thumbnail (e.g. "DAX Fundamentals"); the thumbnail comes back and shows its image preview.

---

## Stage 8 — Processing-log screen  (medium)
**Goal:** the trust screen.
- Summary counts (total / processed / in progress / failed).
- **[REMINDER — separate two categories]** Distinguish "Failed" (a real error, e.g. a broken/corrupt/password-protected file) from "Skipped / unsupported" (a file type we deliberately do not handle, e.g. .zip). Do not lump unsupported types under "Failed" — it wrongly implies something is broken. Show them as separate counts/sections.
- "Run indexing" button that calls `index_assets()`.
- Table of files with status, slide/page count, processed-time, failure reason.
- Failed-files section with plain reasons; separate Skipped/unsupported section.
- Per-day feed ("N files processed on DATE", expandable to filenames).
**Test:** run indexing from the button; counts and table update; a deliberately broken file shows as failed with a reason; a .zip shows as skipped/unsupported, not failed.

---

## Stage 9 — Polish + deploy  (medium)
**Goal:** demo-ready.
- Tidy layout, readable fonts, sensible spacing (match the Stitch design chosen).
- Write the full README (install, run, add files, dataset notes, prototype/production gaps).
- Deploy to Streamlit Community Cloud (free) or run locally for the demo. Note: if the vision API needs a key, keep it in secrets, not in the repo.
**Test:** a clean run-through of the whole demo script works end to end.

---

## Stage 10 (OPTIONAL, deferred) — Content-type filter
**Goal:** filter results by type after a search. Build only if time allows, after everything else works.
- Add Deck / PDF / Image filter toggles above the results; filter the already-returned results.
**Test:** run a search, toggle to Images only, see only image results.

---

## Stage 11 (OPTIONAL, deferred) — Excel as a link source
**Goal:** handle the client's real use of Excel — spreadsheets that hold URLs of their LinkedIn posts and YouTube videos. NOT to read spreadsheet data as content.
- Read the Excel file, extract the column(s) of URLs (plain code, no AI).
- Record each URL as a known asset with its title/link and source type (YouTube / LinkedIn / website).
- Prototype scope: just surface that these links exist ("found N links: X YouTube, Y LinkedIn"). Do NOT fetch YouTube captions in this stage — that is a further extension.
- LinkedIn links: store title + URL only (never fetch gated content).
**Test:** point it at the links spreadsheet; it lists the URLs found, grouped by source type.

---

## Stage 12 (OPTIONAL, deferred, only if client confirms need) — Zip handling
**Goal:** only if Codebasics confirms they actually store content inside zip files.
- Auto-unzip into a temp location, send contents (decks/PDFs/images) through the normal pipeline, link results back to the ORIGINAL file location. User does nothing manually.
- If not confirmed as a real need, leave zips as "skipped / unsupported" in the Processing log (see Stage 8).
**Test:** a zip containing a deck gets its slides indexed and searchable.

---

## R3 — YouTube transcripts (via Excel link file)  [CURRENT CHAPTER]
Client-requested: make their YouTube videos searchable by what was said. Scope: YouTube only. Skip LinkedIn (gated) and websites (messy; possible later extension). Build in small stages, prove the risky caption-fetch on ONE video first.

Source: `public_links.xlsx` in the assets folder. Columns: Title/Description, url, source_type. source_type values: youtube, linkedin, website. Keep only `youtube` rows.

**R3.1 — Read Excel, extract YouTube URLs.** Open the xlsx, read the three columns, filter to source_type == youtube. Plain code, no AI. Test: lists the YouTube URLs and titles found; ignores linkedin/website rows.

**R3.2 — Fetch ONE transcript.** For a single YouTube URL, fetch its public captions/transcript. Prove it works on one video before looping. Handle "no captions" without crashing. Test: prints the transcript text for one video.

**R3.3 — Index all transcripts.** Loop the YouTube URLs, fetch each transcript, index the text like any other content, with video title + URL as metadata + a "YouTube" type. Flag videos with no captions as skipped-with-reason (like unsupported files), never crash. Respect only-new/changed logic so re-runs don't re-fetch everything. Test: search a topic spoken in a video; the video comes back.

**R3.4 — Show video results.** A video result shows: title, a "YouTube" type tag, the matching transcript snippet (highlighted), a "why it matched" reason, and a link that opens the video. No local file path (it's a URL). Test: a transcript match renders as a clean video result with a working link.

Honest caveats for README: captions = spoken words only, not on-screen visuals; not every video has usable captions (those are flagged); public videos only in the prototype.

---

## Tips for each session
- Start by pasting `BUILD_SPEC.md`, then the one stage.
- After each stage, ask Claude Code to run it and show the result before moving on.
- If a stage breaks, stay on it — do not move forward with something broken.
- Commit (save) after each working stage so you can always go back.
- Use Opus 4.8 (high effort) as default; escalate effort only when stuck.
