# extract_pages.py

import sys

from pypdf import PdfReader, PdfWriter


def parse_pages(pages_str: str) -> list[int]:
    """
    Supports:
      "1,2,3"  →  [1, 2, 3]
      "1-5"    →  [1, 2, 3, 4, 5]
      "1,3-5"  →  [1, 3, 4, 5]
    """
    result = []
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            result.extend(range(int(start), int(end) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def extract_pages(input_path: str, pages_str: str, output_path: str):
    reader = PdfReader(input_path)
    total = len(reader.pages)
    print(f"PDF has {total} pages.")

    pages = parse_pages(pages_str)

    invalid = [p for p in pages if p < 1 or p > total]
    if invalid:
        print(f"Error: pages {invalid} don't exist in this PDF.")
        sys.exit(1)

    writer = PdfWriter()
    for page_num in pages:
        writer.add_page(reader.pages[page_num - 1])

    with open(output_path, "wb") as f:
        writer.write(f)

    print(f"Done! Extracted pages {pages} → {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python extract_pages.py <input.pdf> <pages> <output.pdf>")
        print("Examples:")
        print("  python extract_pages.py form.pdf 1,2   output.pdf")
        print("  python extract_pages.py form.pdf 1-5   output.pdf")
        print("  python extract_pages.py form.pdf 1,3-5 output.pdf")
        sys.exit(1)

    input_pdf = sys.argv[1]
    pages = sys.argv[2]
    output_pdf = sys.argv[3]

    extract_pages(input_pdf, pages, output_pdf)


# # Single page
# python extract_pages.py form.pdf 1 page1.pdf

# # Specific pages
# python extract_pages.py form.pdf 1,3,5 selected.pdf

# # Page range
# python extract_pages.py form.pdf 1-5 first5.pdf

# # Mix of both
# python extract_pages.py form.pdf 1,3-5 mixed.pdf
# pypdf is already installed in your project so no extra installs
# needed — just run it from inside your .venv:
# bashsource .venv/bin/activate
# python extract_pages.py form.pdf 1,2 output.pdf
