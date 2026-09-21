"""Rebuild ``medwatch_ocr/templates/fda_1932_2023.json`` from the blank FDA form.

The committed template is the field geometry of the published blank
``FORM FDA 1932 (8/23)``: for every AcroForm widget its page, key, type,
rectangle in PDF points and - for checkboxes - the appearance state that means
"checked".  Run this only when FDA republishes the form.

    python scripts/build_1932_template.py
"""

from __future__ import annotations

import json
import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.form_1932 import TEMPLATE_PATH, blank_form_path, field_key  # noqa: E402


def build(blank_path: str) -> dict:
    reader = PdfReader(blank_path)
    pages = {}
    fields = []
    for page_index, page in enumerate(reader.pages):
        pages[str(page_index)] = {
            "width": round(float(page.mediabox.width), 3),
            "height": round(float(page.mediabox.height), 3),
        }
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            names = []
            node = widget
            while node is not None:
                title = node.get("/T")
                if title:
                    names.append(str(title))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            if not names:
                continue
            qualified = ".".join(reversed(names))
            field_type = widget.get("/FT")
            node = widget
            while field_type is None and node is not None:
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
                field_type = node.get("/FT") if node is not None else None
            on_state = None
            appearances = widget.get("/AP")
            if appearances and "/N" in appearances:
                states = [str(state) for state in appearances["/N"].keys() if state != "/Off"]
                on_state = states[0] if states else None
            spec = {
                "page": page_index,
                "name": field_key(qualified),
                "field": qualified,
                "type": str(field_type).lstrip("/") if field_type else "Tx",
                "rect": [round(float(v), 3) for v in widget["/Rect"]],
            }
            if on_state:
                spec["on"] = on_state
            fields.append(spec)
    return {
        "form": "FDA-1932",
        "revision": "08/2023",
        "source": "fda.gov/media/124792",
        "pages": pages,
        "fields": fields,
    }


def main() -> None:
    template = build(blank_form_path())
    with open(TEMPLATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(template, handle, indent=1)
        handle.write("\n")
    checkboxes = sum(1 for spec in template["fields"] if spec["type"] == "Btn")
    print(f"{TEMPLATE_PATH}: {len(template['fields'])} widgets ({checkboxes} checkboxes) on {len(template['pages'])} pages")


if __name__ == "__main__":
    main()
