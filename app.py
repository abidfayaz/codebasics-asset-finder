"""
Codebasics Asset Finder - Streamlit app.

Stage 2: the Search tab now returns real results from the indexed files.
The Processing log tab is still a placeholder (that is Stage 8).

Run it with:  streamlit run app.py
"""

import html

import streamlit as st

import config
import indexer
import llm
import search as search_module

# Browser tab title and page width. Must be the first Streamlit call.
st.set_page_config(page_title="Asset Finder", page_icon="🔎", layout="wide")

# The one accent colour, used for buttons, highlights and the best-match
# banner. Change it here and the whole app follows.
ACCENT = "#4F46E5"
ACCENT_SOFT = "#EEF0FE"
ACCENT_EDGE = "#D9DDFB"
MUTED = "#6B7280"

# A small amount of styling on top of Streamlit's defaults: a comfortable page
# width, consistent spacing between cards, and readable type sizes.
st.markdown(
    f"""
    <style>
      /* Keep the page from stretching too wide to read on a big monitor. */
      .block-container {{ max-width: 1180px; padding-top: 2.2rem;
                          padding-bottom: 3rem; }}

      /* Even spacing between result cards. */
      div[data-testid="stVerticalBlockBorderWrapper"] {{
          margin-bottom: 0.85rem; }}

      /* Headings: a clear step down in size, not shouty. */
      h1 {{ font-size: 1.9rem !important; font-weight: 700 !important;
            letter-spacing: -0.01em; }}
      h2 {{ font-size: 1.35rem !important; font-weight: 650 !important;
            margin-top: 1.6rem !important; }}
      h3 {{ font-size: 1.05rem !important; font-weight: 650 !important;
            margin-top: 1.4rem !important; margin-bottom: 0.2rem !important; }}

      /* Body text a touch larger than the default for comfortable reading. */
      .stMarkdown p {{ font-size: 0.94rem; line-height: 1.6; }}

      /* Tabs: roomier, with the accent colour marking the active one. */
      button[data-baseweb="tab"] {{ font-size: 1rem; padding: 0.4rem 0.1rem; }}
      div[data-baseweb="tab-highlight"] {{ background-color: {ACCENT}; }}

      /* Recent-search chips: pill shaped and quiet, so they do not compete
         with the search button. */
      div[data-testid="column"] button[kind="secondary"] {{
          border-radius: 999px; font-size: 0.82rem; padding: 0.2rem 0.9rem;
          color: {MUTED}; border-color: #E2E4EE; font-weight: 500; }}
      div[data-testid="column"] button[kind="secondary"]:hover {{
          border-color: {ACCENT}; color: {ACCENT}; }}

      /* The summary numbers on the processing screen. */
      div[data-testid="stMetricValue"] {{ font-size: 1.7rem; }}
      div[data-testid="stMetricLabel"] {{ font-size: 0.82rem; color: {MUTED}; }}

      /* File paths are one long unbroken string - let them wrap rather than
         forcing a sideways scrollbar. */
      div[data-testid="stCode"] code {{
          white-space: pre-wrap !important; overflow-wrap: anywhere !important;
          font-size: 0.78rem !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Loading the model and the database
#
# Both take a few seconds to open, so we do it once and Streamlit keeps them
# in memory. Without this they would reload on every single click.
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading the search model (first time only)...")
def load_model():
    return indexer.get_embedding_model()


@st.cache_resource(show_spinner="Opening the search database...")
def load_collection():
    return indexer.get_collection()


# How many past searches to keep as clickable chips. These live only for as
# long as the browser tab is open - nothing is written to disk.
RECENT_LIMIT = 5


def remember_search(query: str) -> None:
    """
    Add a search to the recent list, newest first, with no duplicates.
    Searching something again moves it back to the front.
    """
    recents = st.session_state.setdefault("recent_searches", [])
    if query in recents:
        recents.remove(query)
    recents.insert(0, query)
    # Keep only the most recent few.
    del recents[RECENT_LIMIT:]


def render_recent_chips() -> None:
    """
    Show past searches as buttons. Clicking one runs that search again.
    """
    recents = st.session_state.get("recent_searches", [])
    if not recents:
        return

    st.caption("Recent searches")
    columns = st.columns(len(recents))
    for column, past_query in zip(columns, recents):
        with column:
            if st.button(past_query, key=f"chip_{past_query}",
                         use_container_width=True):
                # Note which chip was clicked and start the page again. The
                # search box is actually filled in at the top of the next run,
                # because Streamlit will not let us change a box that has
                # already been drawn on this run.
                st.session_state["pending_chip"] = past_query
                st.rerun()


def get_reasons(query: str, results: list[dict],
                weak: bool = False) -> tuple[list[tuple], str | None]:
    """
    Work out a "why it matched" sentence for EVERY result.

    Only the top few are worth paying an AI call for (see WHY_TOP_N). The rest
    get the free word-overlap explanation, so no card is ever left blank.

    Returns a list of (sentence, source) pairs in result order - where source
    is "ai" or "words" - plus any problem message from the AI service.

    Both are kept in session memory against the exact search text, so clicking
    around the page does not fire the same AI request over and over.
    """
    cache = st.session_state.setdefault("reason_cache", {})
    cache_key = (query, weak)
    if cache_key in cache:
        return cache[cache_key]

    paid_for = results[: config.WHY_TOP_N]
    the_rest = results[config.WHY_TOP_N:]

    sentences = llm.why_it_matched(query, paid_for, weak=weak)
    error = llm.last_error

    # If there is no key, or the call failed, those top sentences are word
    # overlap too - so label them honestly rather than claiming they are AI.
    top_source = "ai" if (llm.is_configured() and not error) else "words"

    entries = [(sentence, top_source) for sentence in sentences]
    # The rest cost nothing: no API call, no waiting.
    entries += [(llm.simple_reason(query, r["text"], weak), "words")
                for r in the_rest]

    # Store the error together with the reasons, so a cached result keeps
    # showing the message that actually belongs to it.
    cache[cache_key] = (entries, error)
    return cache[cache_key]


def render_table(rows: list[dict]) -> None:
    """
    Draw a plain HTML table.

    Streamlit's own table needs pandas, and pandas will not load on every
    machine - Windows Application Control blocks one of its files on some
    setups. Writing the table by hand keeps this screen working everywhere.
    """
    if not rows:
        return

    headers = list(rows[0].keys())

    head = "".join(
        f"<th style='text-align:left; padding:8px 10px; position:sticky; "
        f"top:0; background:#f2f3f6; border-bottom:2px solid #dcdde3; "
        f"white-space:nowrap;'>{html.escape(h)}</th>"
        for h in headers
    )

    body = []
    for number, row in enumerate(rows):
        stripe = "#ffffff" if number % 2 == 0 else "#fafafc"
        cells = "".join(
            f"<td style='padding:7px 10px; border-bottom:1px solid #ecedf1; "
            f"vertical-align:top;'>{html.escape(str(row[h]))}</td>"
            for h in headers
        )
        body.append(f"<tr style='background:{stripe};'>{cells}</tr>")

    st.markdown(
        f"<div style='max-height:430px; overflow:auto; border:1px solid "
        f"#e3e4e9; border-radius:6px;'>"
        f"<table style='width:100%; border-collapse:collapse; "
        f"font-size:0.86rem;'>"
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody>"
        f"</table></div>",
        unsafe_allow_html=True,
    )


def render_preview(result: dict, query: str) -> None:
    """
    Show what the result actually looks like.

    Images: the picture itself, with the description underneath.
    Deck slides: a slide-shaped frame holding the slide's text.
    PDFs: just the matching text.

    TODO: the deck preview is a stand-in. Rendering the real slide to a picture
    would look better but needs PowerPoint or LibreOffice installed, which is
    too much to ask of a prototype. BUILD_SPEC section 6.6 allows this version.
    """
    highlighted = search_module.highlight(result["snippet"], query)

    if result["type"] == "image":
        picture, caption = st.columns([1, 2])
        with picture:
            # Found via the assets folder, so this works whether the app is
            # running on the machine that built the index or on a server.
            picture_file = search_module.local_file(result)
            try:
                if picture_file:
                    st.image(str(picture_file), use_container_width=True)
                else:
                    st.caption("(picture not available here)")
            except Exception:
                # A missing or unreadable file must not break the whole page.
                st.caption("(preview unavailable)")
        with caption:
            st.caption("What the image shows")
            st.markdown(highlighted, unsafe_allow_html=True)
        return

    if result["type"] == "youtube":
        # The words spoken in this stretch of the video. Labelled clearly,
        # because it is a quote from the soundtrack - not text on screen.
        st.markdown(
            f"<div style='border:1px solid #d8dae2; border-radius:6px; "
            f"background:#fbfbfd; padding:16px 20px; margin:6px 0 10px 0; "
            f"box-shadow:0 1px 3px rgba(0,0,0,0.06); overflow-wrap:anywhere;'>"
            f"<div style='font-size:0.68rem; letter-spacing:0.08em; "
            f"color:#9aa0a6; margin-bottom:6px;'>"
            f"SPOKEN IN THIS VIDEO · "
            f"{html.escape(result['location_label'].upper())}</div>"
            f"<div style='font-size:0.95rem; line-height:1.55;'>"
            f"“{highlighted}”</div></div>",
            unsafe_allow_html=True,
        )
        return

    if result["type"] == "deck":
        # A slide-shaped box: pale background, slide number above the text.
        # If the match came from a picture on the slide rather than typed
        # text, say so - otherwise the wording looks like it was written there.
        label = result["location_label"].upper()
        if result.get("source") == "picture":
            label += " · FROM A PICTURE ON THIS SLIDE"
        st.markdown(
            f"<div style='border:1px solid #d8dae2; border-radius:6px; "
            f"background:#fbfbfd; padding:18px 22px; margin:6px 0 10px 0; "
            f"min-height:120px; box-shadow:0 1px 3px rgba(0,0,0,0.06); "
            f"overflow-wrap:anywhere;'>"
            f"<div style='font-size:0.68rem; letter-spacing:0.08em; "
            f"color:#9aa0a6; margin-bottom:6px;'>"
            f"{html.escape(label)}</div>"
            f"<div style='font-size:0.95rem; line-height:1.5;'>"
            f"{highlighted}</div></div>",
            unsafe_allow_html=True,
        )
        return

    # PDFs and anything else: the text on its own.
    st.markdown(
        f"<div style='font-size:0.94rem; line-height:1.55; "
        f"overflow-wrap:anywhere;'>{highlighted}</div>",
        unsafe_allow_html=True,
    )


def confidence_pill(confidence: str, align: str = "right") -> str:
    """The little rounded label saying how good the match is."""
    strong = confidence.startswith("Strong")
    text_colour = ACCENT if strong else MUTED
    background = ACCENT_SOFT if strong else "#F1F2F5"
    return (
        f"<div style='text-align:{align};'>"
        f"<span style='background:{background}; color:{text_colour}; "
        f"font-size:0.72rem; font-weight:600; padding:3px 10px; "
        f"border-radius:999px; white-space:nowrap;'>{confidence}</span></div>"
    )


def render_result_card(result: dict, query: str, weak: bool = False) -> None:
    """
    Draw one search result.

    The rank number and the "why it matched" sentence are carried on the
    result itself, so the card has everything it needs.
    """
    rank = result.get("rank")
    reason = result.get("reason")

    with st.container(border=True):
        # Line 1: file name on the left, how confident we are on the right.
        left, right = st.columns([5, 1])
        with left:
            st.markdown(f"**{rank}. {result['file_name']}**")
        with right:
            st.markdown(confidence_pill(result["confidence"]),
                        unsafe_allow_html=True)

        # Line 2: what kind of file, and which slide/page inside it.
        bits = [search_module.type_label(result["type"])]
        if result["location_label"]:
            bits.append(result["location_label"])
        st.caption(" · ".join(bits))

        # Line 3: why this came back, in one plain sentence.
        # Blue with an "AI" tag = written by the AI service (top results).
        # Grey with a "word check" tag = the free explanation (all the rest).
        if reason:
            if result.get("reason_source") == "ai":
                tint, edge, tag = ACCENT_SOFT, ACCENT, "AI"
            else:
                tint, edge, tag = "#F4F5F7", "#9AA0A6", "word check"

            st.markdown(
                f"<div style='background-color:{tint}; "
                f"border-left:3px solid {edge}; padding:8px 12px; "
                f"border-radius:4px; margin-bottom:10px; "
                f"font-size:0.9rem; line-height:1.5; "
                f"overflow-wrap:anywhere;'>"
                f"<span style='display:inline-block; background-color:{edge}; "
                f"color:white; font-size:0.66rem; font-weight:700; "
                f"padding:1px 6px; border-radius:8px; margin-right:8px; "
                f"vertical-align:middle;'>{tag}</span>"
                # In the "closest few" state, nothing matched - so calling this
                # "why it matched" would contradict the banner above it.
                f"<b>{'What this covers' if weak else 'Why it matched'}:</b> "
                f"{html.escape(reason)}</div>",
                unsafe_allow_html=True,
            )

        # Line 4: a look at the thing itself, so you can recognise it without
        # opening the file.
        render_preview(result, query)

        # Line 5: how to get to the thing itself.
        if result["type"] == "youtube":
            # A video is not a file, so showing a "file location" would be a
            # lie. Give a link that actually opens it instead.
            link = search_module.web_link(result)
            if link:
                st.markdown(
                    f"<a href='{html.escape(link)}' target='_blank' "
                    f"rel='noopener noreferrer' "
                    f"style='display:inline-block; background:{ACCENT}; "
                    f"color:#ffffff; text-decoration:none; font-weight:600; "
                    f"font-size:0.85rem; padding:7px 16px; border-radius:6px; "
                    f"margin-top:2px;'>▶ Watch on YouTube</a>"
                    f"<div style='font-size:0.72rem; color:{MUTED}; "
                    f"margin-top:6px; overflow-wrap:anywhere;'>"
                    f"{html.escape(link)}</div>",
                    unsafe_allow_html=True,
                )
            else:
                # Better to show nothing than a button that goes nowhere.
                st.caption("Video address unavailable for this result.")
        else:
            # For real files: where it is, with a copy button for free. Shows
            # the folder on this machine when the file is here, and the short
            # path inside the assets folder when it is not - never a path from
            # somebody else's computer.
            st.caption("File location")
            st.code(search_module.display_location(result), language=None)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("🔎 Codebasics Asset Finder")
st.caption("Search your decks, PDFs and images by meaning - not by filename.")

tab_search, tab_log = st.tabs(["Search", "Processing log"])

with tab_search:
    st.header("Search")

    # What the search box should contain, and a counter used to force a fresh
    # box when a chip is clicked (see below).
    st.session_state.setdefault("box_value", "")
    st.session_state.setdefault("box_version", 0)

    # If a recent-search chip was clicked on the previous run, apply it now -
    # before the search box is drawn.
    clicked_chip = st.session_state.pop("pending_chip", None)
    if clicked_chip:
        st.session_state["box_value"] = clicked_chip
        st.session_state["last_query"] = clicked_chip
        # Streamlit keeps whatever you typed in a form box, even when the
        # value behind it changes. Bumping the version gives the box a new
        # key, so it is rebuilt from scratch showing the chip's text.
        st.session_state["box_version"] += 1

    # Reserve the spot above the search box for the recent-search chips. They
    # are drawn later, once we know what the current search is, but they
    # appear here on the page.
    chip_area = st.container()

    # A form means pressing Enter in the box runs the search, not just the
    # button.
    with st.form("search_form"):
        query = st.text_input(
            "What are you looking for?",
            value=st.session_state["box_value"],
            key=f"search_box_{st.session_state['box_version']}",
            placeholder="e.g. star schema, or how to build a resume",
        )
        submitted = st.form_submit_button("Search", type="primary")

    if submitted:
        # Remember the query so results survive Streamlit's reruns.
        st.session_state["last_query"] = query
        st.session_state["box_value"] = query

    current_query = st.session_state.get("last_query", "")

    if current_query:
        remember_search(current_query)

    # Now draw the chips into the space reserved above the box.
    with chip_area:
        render_recent_chips()

    if current_query:
        collection = load_collection()

        if collection.count() == 0:
            st.warning(
                "Nothing has been indexed yet, so there is nothing to search. "
                "Run the indexer first:  python indexer.py"
            )
        else:
            model = load_model()
            with st.spinner("Searching..."):
                results = search_module.search(
                    current_query, collection=collection, model=model
                )

            # Did anything clear the quality bar? If not we still show the
            # closest few, rather than a dead end.
            weak_search = bool(results) and not search_module.has_strong_match(results)
            if weak_search:
                results = results[: config.CLOSEST_FEW]

            if not results:
                # Only happens when the library itself is empty of matches.
                st.info("No results found.")
            else:
                with st.spinner("Working out why these matched..."):
                    reasons, reason_error = get_reasons(
                        current_query, results, weak=weak_search)

                # Attach the position and the reason to each result, so the
                # cards carry everything they need however they are arranged.
                for position, result in enumerate(results, start=1):
                    result["rank"] = position
                    if position <= len(reasons):
                        result["reason"], result["reason_source"] = reasons[position - 1]
                    else:
                        result["reason"] = None
                        result["reason_source"] = None

                if weak_search:
                    # Nothing was a confident match. Say so plainly, then show
                    # the closest anyway so there is somewhere to go next.
                    st.warning(
                        f"**Nothing matched exactly — here are the "
                        f"{len(results)} closest.**  \n"
                        f"These are the nearest things in your library to "
                        f"\"{current_query}\". They may not be what you want."
                    )
                else:
                    st.caption(
                        f"{len(results)} results for \"{current_query}\", "
                        f"closest first"
                    )

                if not llm.is_configured():
                    st.caption(
                        "No AI key set up, so the reasons below are simple "
                        "word-overlap explanations. Add LLM_API_KEY to your "
                        ".env for AI-written ones."
                    )
                elif reason_error:
                    # The AI call failed - say so rather than pretending.
                    st.warning(f"Showing simple reasons: {reason_error}.")

                # One ranked list, best first, whatever was typed. However many
                # words the search has, every one of them is highlighted in the
                # results below.
                for result in results:
                    render_result_card(result, current_query, weak=weak_search)

with tab_log:
    st.header("Processing log")
    st.caption(
        "What the app has read, what it skipped, and anything that went wrong. "
        "Use this to check whether a file you just added has been picked up."
    )

    status = indexer.load_status()
    files = status.get("files", {})

    # ----- The button that does the reading -----------------------------
    if config.READ_ONLY:
        # Published copy: it carries a ready-made index, and the source decks
        # are not shipped with it, so there is nothing here to re-read.
        run_now = False
        st.info(
            "This published copy searches a ready-made index of the sample "
            "content, so there is nothing to re-index here. Everything below "
            "is the record of when that content was read."
        )
        if status.get("last_run"):
            st.caption(f"Indexed on: {status['last_run'].replace('T', ' at ')}")
    else:
        left, right = st.columns([1, 3])
        with left:
            run_now = st.button("Run indexing", type="primary")
        with right:
            if status.get("last_run"):
                st.caption(f"Last run: {status['last_run'].replace('T', ' at ')}")
            else:
                st.caption("Never run yet.")

    # A message left over from an indexing run that just finished.
    finished_message = st.session_state.pop("index_result", None)
    if finished_message:
        if finished_message.startswith("Indexing stopped"):
            st.error(finished_message)
        else:
            st.success(finished_message)

    if run_now:
        with st.spinner(
            "Reading your files. New images are sent to the vision model one "
            "at a time, so this can take a few minutes."
        ):
            try:
                summary = indexer.index_assets()
                # .get() throughout: if this screen is ever running against a
                # slightly older indexer (Streamlit reloads app.py but not the
                # other files until you restart), a missing count should just
                # read as zero rather than throwing an error at you.
                message = (
                    f"Done. {summary.get('processed', 0)} newly indexed, "
                    f"{summary.get('skipped_unchanged', 0)} unchanged, "
                    f"{summary.get('failed', 0)} failed, "
                    f"{summary.get('skipped_unsupported', 0)} skipped."
                )
                if summary.get("removed_missing"):
                    gone = ", ".join(summary.get("removed_files", []))
                    message += (
                        f"  Removed {summary['removed_missing']} file(s) that "
                        f"are no longer on disk: {gone}."
                    )
                st.session_state["index_result"] = message
                # The search side keeps the database open in memory, so clear
                # it - otherwise searches would not see what we just added.
                st.cache_resource.clear()
            except Exception as error:
                st.session_state["index_result"] = f"Indexing stopped: {error}"

        # Draw the whole screen again, so the counts, the table and the
        # "last run" time all show what just happened rather than what was
        # true a moment ago.
        st.rerun()

    if not files:
        st.info(
            "Nothing has been indexed yet. Press **Run indexing** above, or "
            "run `python indexer.py` in a terminal."
        )
    else:
        # ----- Summary counts -------------------------------------------
        counts = {"processed": 0, "in_progress": 0, "failed": 0, "skipped": 0}
        for record in files.values():
            if record["status"] in counts:
                counts[record["status"]] += 1

        total_chunks = sum(r.get("chunks", 0) for r in files.values())

        st.subheader("Summary")
        boxes = st.columns(5)
        boxes[0].metric("Files seen", len(files))
        boxes[1].metric("Processed", counts["processed"])
        boxes[2].metric("In progress", counts["in_progress"])
        boxes[3].metric("Failed", counts["failed"])
        boxes[4].metric("Skipped", counts["skipped"])
        st.caption(
            f"{total_chunks} searchable pieces indexed in total.  ·  "
            f"**Failed** means something is wrong with the file. "
            f"**Skipped** means a file type this app does not handle - "
            f"nothing is broken."
        )

        # ----- The full table -------------------------------------------
        st.subheader("All content")
        st.caption("Most recently processed first.")

        # Friendly words instead of the internal status names.
        status_words = {
            "processed": "✅ Processed",
            "in_progress": "⏳ In progress",
            "failed": "❌ Failed",
            "skipped": "⚪ Skipped",
        }
        unit_words = {"deck": "slides", "pdf": "pages", "image": "description"}

        # Newest first: the file you added a minute ago should be at the top,
        # which is what this screen is mostly used to check. Anything never
        # actually read (skipped types) has no time, so it sits at the bottom
        # in name order rather than jumping to the top.
        def newest_first(record):
            return (record.get("indexed_time") or "", record["file_name"].lower())

        ordered = sorted(files.values(), key=newest_first, reverse=True)

        rows = []
        for record in ordered:
            rows.append({
                "File": record["file_name"],
                "Type": search_module.type_label(record["type"]),
                "Status": status_words.get(record["status"], record["status"]),
                "Indexed": (
                    f"{record.get('chunks', 0)} "
                    f"{unit_words.get(record['type'], 'pieces')}"
                    if record.get("chunks") else "-"
                ),
                "Processed at": (record.get("indexed_time") or "-").replace("T", " "),
                "Note / reason": record.get("reason") or "",
            })

        render_table(rows)

        # ----- Failures: things that are actually wrong ------------------
        failed = [r for r in files.values() if r["status"] == "failed"]
        st.subheader(f"Failed files ({len(failed)})")
        if not failed:
            st.success("No failures. Every supported file was read successfully.")
        else:
            st.caption("Something went wrong reading these. They are worth a look.")
            for record in failed:
                st.error(f"**{record['file_name']}** — {record['reason']}")

        # ----- Skipped: deliberate, not a problem ------------------------
        skipped = [r for r in files.values() if r["status"] == "skipped"]
        st.subheader(f"Skipped files ({len(skipped)})")
        if not skipped:
            st.caption("Nothing skipped.")
        else:
            st.caption(
                "Not indexed as searchable documents, on purpose - nothing is "
                "wrong with them. Either the app does not handle that file "
                "type, or the file is used for something else (the link "
                "spreadsheet is read separately, to find your YouTube videos)."
            )
            for record in skipped:
                st.info(f"**{record['file_name']}** — {record['reason']}")

        # ----- Per-day feed ---------------------------------------------
        st.subheader("Processing history")
        st.caption("Newest first. Open a day to see exactly which files were read.")

        by_day = {}
        for record in files.values():
            stamp = record.get("indexed_time")
            if not stamp:
                continue  # never actually read (skipped files have no time)
            day = stamp.split("T")[0]
            by_day.setdefault(day, []).append(record)

        if not by_day:
            st.caption("Nothing has been read yet.")
        else:
            for day in sorted(by_day, key=lambda d: d, reverse=True):
                day_files = sorted(by_day[day], key=lambda r: r["indexed_time"],
                                   reverse=True)
                good = sum(1 for r in day_files if r["status"] == "processed")
                bad = sum(1 for r in day_files if r["status"] == "failed")
                headline = f"{good} files processed on {day}"
                if bad:
                    headline += f"  ({bad} failed)"

                with st.expander(headline):
                    for record in day_files:
                        mark = status_words.get(record["status"], "")
                        when = record["indexed_time"].split("T")[1]
                        line = f"{mark} `{when}`  {record['file_name']}"
                        if record.get("reason"):
                            line += f" — {record['reason']}"
                        st.markdown(line)

# A small footer so you can confirm the config file is being read correctly.
st.divider()
st.caption(f"Assets folder: {config.ASSETS_FOLDER}")
if not config.ASSETS_FOLDER.exists():
    st.warning(
        f"That folder does not exist yet. Create it, or point ASSETS_FOLDER "
        f"in your .env file at the folder holding your files."
    )
