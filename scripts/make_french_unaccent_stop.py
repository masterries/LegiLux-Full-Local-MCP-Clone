#!/usr/bin/env python3
"""Build db/tsearch/french_unaccent.stop = stock PostgreSQL french.stop, unaccented, deduplicated, sorted.

Usage: docker run --rm --entrypoint cat <pgvector image> /usr/share/postgresql/18/tsearch_data/french.stop \
         | python3 scripts/make_french_unaccent_stop.py > db/tsearch/french_unaccent.stop
"""

import sys
import unicodedata


def unaccent(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def main() -> None:
    words = {unaccent(line.strip().lower()) for line in sys.stdin}
    sys.stdout.writelines(w + "\n" for w in sorted(words) if w)


if __name__ == "__main__":
    main()
