#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path
from datetime import date

import requests

STATUS_FILE = Path("status.json")
MOVIES_FILE = Path("movies.json")
OUTPUT_MARKDOWN = Path("streaming_matrix.md")

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
WATCH_REGION = "US"

# Abort without saving when more than this share of movies can't be checked,
# so an outage or bad API key doesn't overwrite good data.
MAX_FAILURE_RATIO = 0.5

# Exact TMDB provider names (after normalize_provider_name) that count as each
# service. Exact matching keeps out add-on channels such as "Max Amazon Channel",
# "Paramount+ Amazon Channel" and lookalikes such as "Cinemax".
SERVICE_PROVIDER_NAMES = {
    "netflix_us": {
        "netflix",
        "netflix basic with ads",
        "netflix standard with ads",
    },
    "prime_us_included": {
        "amazon prime video",
        "amazon prime video with ads",
    },
    "max_us": {
        "max",
        "hbo max",
    },
    "paramount_us_included": {
        "paramount plus",
        "paramount plus with showtime",
        "paramount plus essential",
        "paramount plus premium",
    },
}


class LookupFailed(Exception):
    """A TMDB request failed, so availability is unknown (not "unavailable")."""


# ---------- helpers ----------

def load_movies():
    with MOVIES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_status():
    if not STATUS_FILE.exists():
        return {}
    with STATUS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_status(status):
    with STATUS_FILE.open("w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)


def movie_id(movie):
    # Unique key combining title + year
    return f"{movie['title']} ({movie['year']})"


def tmdb_get(path, params=None):
    """
    Thin wrapper around TMDB's v3 API using an API key.

    Docs:
    - Getting started: https://developer.themoviedb.org/reference/getting-started
    """
    if TMDB_API_KEY is None:
        raise RuntimeError("TMDB_API_KEY environment variable is not set")

    if params is None:
        params = {}
    params = {**params, "api_key": TMDB_API_KEY}

    url = f"{TMDB_BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def find_movie_id_tmdb(title, year, region=WATCH_REGION):
    """
    Use TMDB's /search/movie to find a movie ID by title + year.
    Returns None if TMDB has no match; raises LookupFailed if the request fails.
    """
    try:
        data = tmdb_get(
            "/search/movie",
            {
                "query": title,
                "year": year,
                "include_adult": "false",
                "region": region,
                "page": 1,
            },
        )
    except requests.RequestException as e:
        raise LookupFailed(f"TMDB search failed for {title} ({year}): {e}") from e

    results = data.get("results", [])
    if not results:
        return None

    # Prefer an exact year match if possible
    year_str = str(year)
    for r in results:
        rd = (r.get("release_date") or "")[:4]
        if rd == year_str:
            return r.get("id")

    # Fallback: first result
    return results[0].get("id")


def get_movie_providers_tmdb(movie_id, region=WATCH_REGION):
    """
    Use TMDB's /movie/{movie_id}/watch/providers.
    Returns a list of provider objects (flatrate + ads) in that region;
    raises LookupFailed if the request fails.
    """
    try:
        data = tmdb_get(f"/movie/{movie_id}/watch/providers")
    except requests.RequestException as e:
        raise LookupFailed(f"TMDB providers failed for movie_id={movie_id}: {e}") from e

    results = data.get("results", {})
    region_info = results.get(region)
    if not region_info:
        return []

    # "flatrate" = subscription streaming; "ads" = free-with-ads platforms
    providers = []
    for key in ("flatrate", "ads"):
        providers.extend(region_info.get(key, []))

    return providers


def normalize_provider_name(name: str) -> str:
    # "Paramount+ with Showtime" -> "paramount plus with showtime"
    name = name.lower().replace("+", " plus ")
    return re.sub(r"\s+", " ", name).strip()


def check_movie_availability(title, year):
    """
    Real implementation:
    - Finds TMDB movie ID
    - Fetches watch providers for US
    - Checks for Netflix, Amazon Prime, Max (HBO Max), Paramount+
    Raises LookupFailed if TMDB couldn't be reached.
    """
    movie_id = find_movie_id_tmdb(title, year)
    if not movie_id:
        print(f"[INFO] No TMDB match for {title} ({year})")
        return {
            "netflix_us": False,
            "prime_us_included": False,
            "max_us": False,
            "paramount_us_included": False,
        }

    providers = get_movie_providers_tmdb(movie_id)
    names = {normalize_provider_name(p.get("provider_name", "")) for p in providers}

    return {
        key: bool(names & accepted)
        for key, accepted in SERVICE_PROVIDER_NAMES.items()
    }


# ---------- main status / markdown logic ----------

def update_status(movies, previous_status):
    today_str = date.today().isoformat()
    new_status = {}
    newly_netflix = []
    newly_prime = []
    newly_max = []
    newly_paramount = []
    failures = 0

    for movie in movies:
        mid = movie_id(movie)
        old = previous_status.get(mid, {})

        try:
            avail = check_movie_availability(movie["title"], movie["year"])
        except LookupFailed as e:
            # Keep the last known values instead of recording "unavailable".
            print(f"[WARN] {e}; keeping previous status")
            failures += 1
            new_status[mid] = {
                **old,
                "title": movie["title"],
                "year": movie["year"],
                "watched": bool(movie.get("watched", False)),
                "watched_date": movie.get("watched_date"),
                "netflix_us": bool(old.get("netflix_us", False)),
                "prime_us_included": bool(old.get("prime_us_included", False)),
                "max_us": bool(old.get("max_us", False)),
                "paramount_us_included": bool(old.get("paramount_us_included", False)),
                "last_checked": old.get("last_checked"),
            }
            continue

        netflix = bool(avail.get("netflix_us", False))
        prime = bool(avail.get("prime_us_included", False))
        max_ = bool(avail.get("max_us", False))
        paramount = bool(avail.get("paramount_us_included", False))
        watched = bool(movie.get("watched", False))
        watched_date = movie.get("watched_date")

        new_entry = {
            "title": movie["title"],
            "year": movie["year"],
            "watched": watched,
            "watched_date": watched_date,
            "netflix_us": netflix,
            "prime_us_included": prime,
            "max_us": max_,
            "paramount_us_included": paramount,
            "last_checked": today_str,
        }

        new_status[mid] = new_entry

        # Compare with previous run
        old_netflix = bool(old.get("netflix_us", False))
        old_prime = bool(old.get("prime_us_included", False))
        old_max = bool(old.get("max_us", False))
        old_paramount = bool(old.get("paramount_us_included", False))

        if netflix and not old_netflix:
            newly_netflix.append(new_entry)
        if prime and not old_prime:
            newly_prime.append(new_entry)
        if max_ and not old_max:
            newly_max.append(new_entry)
        if paramount and not old_paramount:
            newly_paramount.append(new_entry)

    return new_status, newly_netflix, newly_prime, newly_max, newly_paramount, failures


def generate_markdown(status):
    rows = list(status.values())
    rows.sort(key=lambda r: (r["title"], r["year"]))

    lines = []
    lines.append("# Streaming Availability Matrix\n")
    lines.append(f"_Last updated: {date.today().isoformat()}_\n")
    lines.append("")
    lines.append(
        "| Watched | Title | Year | Netflix (US) | Prime (US) | Max (US) | Paramount+ (US) | Last Checked |"
    )
    lines.append(
        "| :---: | --- | --- | :---: | :---: | :---: | :---: | --- |"
    )


    def icon(val):
        return "✅" if val else "❌"
    def watched_icon(w):
        return "✅" if w else "—"

    for r in rows:
        wd = r.get("watched_date") or ""
        watched_cell = watched_icon(r.get("watched", False))
        if wd:
            watched_cell = f"{watched_cell} ({wd})"

        lines.append(
            f"| {watched_cell} | {r['title']} | {r['year']} | "
            f"{icon(r['netflix_us'])} | "
            f"{icon(r['prime_us_included'])} | "
            f"{icon(r['max_us'])} | "
            f"{icon(r['paramount_us_included'])} | "
            f"{r.get('last_checked') or 'never'} |"
        )

    OUTPUT_MARKDOWN.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    if not TMDB_API_KEY:
        sys.exit("[ERROR] TMDB_API_KEY environment variable is not set")

    movies = load_movies()
    prev_status = load_status()
    (
        status, newly_netflix, newly_prime, newly_max, newly_paramount, failures
    ) = update_status(movies, prev_status)

    if movies and failures / len(movies) > MAX_FAILURE_RATIO:
        sys.exit(
            f"[ERROR] {failures}/{len(movies)} TMDB lookups failed; "
            "not saving results"
        )

    save_status(status)
    generate_markdown(status)

    # Simple summary for logs (you could email or Slack this instead)
    any_new = any(
        [newly_netflix, newly_prime, newly_max, newly_paramount]
    )

    if not any_new:
        print("No new movies became available on Netflix/Prime/Max/Paramount+ in this run.")
        return

    print("Newly available this run:")

    if newly_netflix:
        print("\nOn Netflix (US):")
        for m in newly_netflix:
            print(f"  - {m['title']} ({m['year']})")

    if newly_prime:
        print("\nIncluded with Amazon Prime (US):")
        for m in newly_prime:
            print(f"  - {m['title']} ({m['year']})")

    if newly_max:
        print("\nOn Max (US):")
        for m in newly_max:
            print(f"  - {m['title']} ({m['year']})")

    if newly_paramount:
        print("\nIncluded with Paramount+ (US):")
        for m in newly_paramount:
            print(f"  - {m['title']} ({m['year']})")


if __name__ == "__main__":
    main()
