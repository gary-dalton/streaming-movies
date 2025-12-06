#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import date

STATUS_FILE = Path("status.json")
MOVIES_FILE = Path("movies.json")
OUTPUT_MARKDOWN = Path("streaming_matrix.md")


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
    # Unique ID combining title + year
    return f"{movie['title']} ({movie['year']})"


def check_movie_availability(title, year):
    """
    TODO: IMPLEMENT ME.

    This stub is where you plug in your real logic:
    - Call a streaming guide API or scrape (e.g., JustWatch)
    - Respect the site's ToS and rate limits
    - Return True/False for each subscription service

    For now, it always returns False for everything so that
    the skeleton runs without external dependencies.
    """
    return {
        "netflix_us": False,
        "prime_us_included": False
    }


def update_status(movies, previous_status):
    today_str = date.today().isoformat()
    new_status = {}
    newly_on_netflix = []
    newly_on_prime = []

    for movie in movies:
        mid = movie_id(movie)
        old = previous_status.get(mid, {})

        avail = check_movie_availability(movie["title"], movie["year"])
        netflix = bool(avail.get("netflix_us", False))
        prime = bool(avail.get("prime_us_included", False))

        new_entry = {
            "title": movie["title"],
            "year": movie["year"],
            "netflix_us": netflix,
            "prime_us_included": prime,
            "last_checked": today_str
        }
        new_status[mid] = new_entry

        # Detect changes vs previous run
        old_netflix = bool(old.get("netflix_us", False))
        old_prime = bool(old.get("prime_us_included", False))

        if netflix and not old_netflix:
            newly_on_netflix.append(new_entry)
        if prime and not old_prime:
            newly_on_prime.append(new_entry)

    return new_status, newly_on_netflix, newly_on_prime


def generate_markdown(status):
    # Turn status dict into a list and sort by title
    rows = list(status.values())
    rows.sort(key=lambda r: (r["title"], r["year"]))

    lines = []
    lines.append("# Streaming Availability Matrix\n")
    lines.append(f"_Last updated: {date.today().isoformat()}_\n")
    lines.append("")
    lines.append("| Title | Year | Netflix (US) | Prime (US, included) | Last Checked |")
    lines.append("| --- | --- | :---: | :---: | --- |")

    def icon(val):
        return "✅" if val else "❌"

    for r in rows:
        lines.append(
            f"| {r['title']} | {r['year']} | "
            f"{icon(r['netflix_us'])} | "
            f"{icon(r['prime_us_included'])} | "
            f"{r['last_checked']} |"
        )

    OUTPUT_MARKDOWN.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    movies = load_movies()
    prev_status = load_status()
    status, newly_netflix, newly_prime = update_status(movies, prev_status)
    save_status(status)
    generate_markdown(status)

    # Print a simple summary for logs / email piping etc.
    if newly_netflix or newly_prime:
        print("Newly available this run:")
        if newly_netflix:
            print("\nOn Netflix (US):")
            for m in newly_netflix:
                print(f"  - {m['title']} ({m['year']})")
        if newly_prime:
            print("\nIncluded with Prime (US):")
            for m in newly_prime:
                print(f"  - {m['title']} ({m['year']})")
    else:
        print("No new movies became available on Netflix/Prime in this run.")


if __name__ == "__main__":
    main()
