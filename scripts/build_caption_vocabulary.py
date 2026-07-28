"""Extract the printed vocabulary of the blank FDA 3500A into a JSON word list.

The caption-anchored reader (:mod:`medwatch_ocr.label_extract`) uses it to tell
the *printed* form from the *typed* values: a font whose words are mostly unknown
to the blank form is a value font.

Usage: python scripts/build_caption_vocabulary.py [--out medwatch_ocr/templates/fda_3500a_caption_words.json]
"""

import argparse
import json
import os
import re
import sys

import pypdfium2 as pdfium

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.official_form import blank_form_path  # noqa: E402

DEFAULT_OUT = os.path.join("medwatch_ocr", "templates", "fda_3500a_caption_words.json")


def caption_words(pdf_path: str) -> list:
    words = set()
    document = pdfium.PdfDocument(pdf_path)
    try:
        for index in range(len(document)):
            text = document[index].get_textpage().get_text_range()
            for token in re.split(r"\s+", text.replace("\u2019", "'")):
                token = token.strip().lower()
                if token:
                    words.add(token)
    finally:
        document.close()
    return sorted(words)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--blank", help="blank 3500A PDF (downloaded from fda.gov when omitted)")
    args = parser.parse_args()

    words = caption_words(args.blank or blank_form_path())
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(words, handle, indent=0)
        handle.write("\n")
    print(f"{len(words)} caption words -> {args.out}")


if __name__ == "__main__":
    main()
