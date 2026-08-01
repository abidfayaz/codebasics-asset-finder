"""
Reads the spreadsheet of public links.

Your assets folder contains an Excel file (`public_links.xlsx`) listing content
that lives on the web rather than in a file: YouTube videos, LinkedIn posts and
web pages. It has three columns:

    Title / Description  |  url  |  source_type

This module opens that file and hands back just the YouTube rows. LinkedIn and
website rows are counted and ignored - LinkedIn is behind a login, and web
pages are messy. Both are noted as possible later additions.

This is plain code. No AI, no network calls, nothing fetched. It only reads
the spreadsheet.

Run it on its own to see what it finds:  python links.py
"""

from pathlib import Path

import config

# The spreadsheet we are looking for. Matched case-insensitively.
LINK_FILE_NAME = "public_links.xlsx"

# The only source_type we handle. The others are recognised so they can be
# reported honestly rather than silently dropped.
WANTED_SOURCE = "youtube"
KNOWN_SOURCES = {"youtube", "linkedin", "website"}

# How we recognise each column, whatever the exact wording of the header.
TITLE_HEADERS = {"title / description", "title/description", "title",
                 "description", "title / desc"}
URL_HEADERS = {"url", "link", "urls"}
SOURCE_HEADERS = {"source_type", "source type", "source", "type"}


def find_link_files(folder=None) -> list[Path]:
    """
    Find the link spreadsheet anywhere in the assets folder.

    Excel leaves a hidden "~$" file behind while a workbook is open; those are
    not real spreadsheets, so they are ignored.
    """
    folder = Path(folder) if folder else config.ASSETS_FOLDER
    if not folder.exists():
        return []

    return sorted(
        path for path in folder.rglob("*.xlsx")
        if path.name.lower() == LINK_FILE_NAME and not path.name.startswith("~$")
    )


def _find_columns(header_row) -> dict:
    """
    Work out which column is which, by looking at the header names.
    Returns {"title": index, "url": index, "source": index}.
    """
    columns = {}
    for index, cell in enumerate(header_row):
        name = str(cell or "").strip().lower()
        if name in TITLE_HEADERS and "title" not in columns:
            columns["title"] = index
        elif name in URL_HEADERS and "url" not in columns:
            columns["url"] = index
        elif name in SOURCE_HEADERS and "source" not in columns:
            columns["source"] = index

    missing = {"title", "url", "source"} - set(columns)
    if missing:
        found = [str(c) for c in header_row]
        raise ValueError(
            f"The link spreadsheet is missing the {', '.join(sorted(missing))} "
            f"column(s). Found these headers instead: {found}. "
            f"Expected: Title / Description, url, source_type."
        )
    return columns


def read_rows(path: Path) -> list[dict]:
    """
    Read every row of one spreadsheet into a list of dictionaries:
        {"title": ..., "url": ..., "source_type": ...}

    Blank rows and rows with no URL are left out.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)

        try:
            header = next(rows)
        except StopIteration:
            return []          # completely empty sheet

        columns = _find_columns(header)

        entries = []
        for row in rows:
            def cell(key):
                index = columns[key]
                return str(row[index]).strip() if index < len(row) and row[index] else ""

            url = cell("url")
            if not url:
                continue       # blank or padding row

            entries.append({
                "title": cell("title"),
                "url": url,
                "source_type": cell("source").lower(),
            })
        return entries
    finally:
        workbook.close()


def get_youtube_links(folder=None) -> tuple[list[dict], dict]:
    """
    Read the link spreadsheet(s) and return only the YouTube rows.

    Returns (youtube_rows, counts) where counts records how many rows were
    skipped and why, so nothing disappears without explanation.

    If the same link appears twice - the sample folder holds two copies of the
    spreadsheet - it is only returned once.
    """
    counts = {
        "files_read": 0,
        "rows_total": 0,      # every row, including repeats
        "rows_unique": 0,     # distinct links
        "youtube": 0,
        "linkedin": 0,
        "website": 0,
        "unknown_source": 0,
        "duplicates": 0,
    }

    wanted = []
    seen_urls = set()

    for path in find_link_files(folder):
        counts["files_read"] += 1
        for row in read_rows(path):
            counts["rows_total"] += 1

            # The sample folder holds two copies of the same spreadsheet, so
            # count each link once - otherwise every total is doubled.
            if row["url"] in seen_urls:
                counts["duplicates"] += 1
                continue
            seen_urls.add(row["url"])
            counts["rows_unique"] += 1

            source = row["source_type"]
            if source in KNOWN_SOURCES:
                counts[source] += 1
            else:
                counts["unknown_source"] += 1
                continue

            if source != WANTED_SOURCE:
                continue       # LinkedIn and websites are out of scope

            wanted.append({"title": row["title"], "url": row["url"]})

    return wanted, counts


def print_report(videos: list[dict], counts: dict) -> None:
    """Print what was found, in a readable shape."""
    print("=" * 70)
    print("YOUTUBE LINKS FOUND")
    print("=" * 70)
    for number, video in enumerate(videos, start=1):
        print(f"{number:3}. {video['title']}")
        print(f"     {video['url']}")

    skipped = counts["linkedin"] + counts["website"] + counts["unknown_source"]

    print()
    print("=" * 70)
    print(f"  Spreadsheets read  : {counts['files_read']}")
    print(f"  Rows read          : {counts['rows_total']}")
    if counts["duplicates"]:
        print(f"  Repeated links     : {counts['duplicates']} "
              f"(counted once each)")
    print(f"  Distinct links     : {counts['rows_unique']}")
    print(f"  YouTube (kept)     : {len(videos)}")
    print(f"  Skipped            : {skipped}")
    print(f"      linkedin       : {counts['linkedin']}")
    print(f"      website        : {counts['website']}")
    if counts["unknown_source"]:
        print(f"      unrecognised   : {counts['unknown_source']}")
    print("=" * 70)


if __name__ == "__main__":
    found, tally = get_youtube_links()
    if not tally["files_read"]:
        print(f"No {LINK_FILE_NAME} found anywhere under {config.ASSETS_FOLDER}.")
    else:
        print_report(found, tally)
