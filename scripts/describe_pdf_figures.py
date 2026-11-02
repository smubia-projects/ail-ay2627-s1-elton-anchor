"""Phase 2, step 5b: Diagram descriptions for the pictures inside a PDF.

Docling reads a PDF's text layer; a graph has none, so it leaves an
`<!-- image -->` placeholder and the graph is invisible to search and to the
answering model. In MidtermSampleSolutions.pdf that is 11 graphs, several of
them the only place a number is given (p. 14: where each marginal-benefit line
meets the axes).

For each picture, in document order:

  1. Crop    the picture's box (from Docling's JSON) out of the rendered page
             -> parsed/figures/<stem>_p<page>_<n>.png
  2. Describe one pass for what it is, its text, and - for a chart - its axes,
             lines, points and shaded areas (FIGURE_PROMPT). Arrows are left
             out of this pass: they are read READINGS times and voted on
             (diagram_connections.py), as for the two PNGs.
  3. Insert  the description replaces that picture's placeholder in
             <stem>.md and <stem>.pages.md, so its chunk cites the PDF page.
             Its "## " headings become bold lines: a heading would open a new
             chunk section and cut the graph off from its question.

Model: GPT-5.6 Luna (record 0005; course material goes to OpenAI only). Tested
before use on the p. 5 and p. 14 graphs, 3 runs each: axes, lines, labelled
points and tick values agreed in 3 of 3 and matched the image; one of 3 runs
misread a small arrow - hence the vote for arrows.

Every PDF is listed, the IS114 PDF too: inside it, its pictures answer its own
text (the FAQ "Where to find the revision quizzes?" is answered only by a
screenshot).

Same picture as a Library image: reuse that image's reviewed description
instead of a new reading. A PDF picture counts as the same when its pixels
correlate >= MATCH with the image (both shrunk to 48x48 grey). On the eval
Library: the IS114 FAQ screenshot vs IS114_eLearn_Quizzes_tab.png 0.59; every
other pair 0.13 or less. Why reuse: every OpenAI model tried (Luna, Terra, Sol,
GPT-5.5, o3) reads that screenshot's arrow as pointing at "Assignments" - its
tip overlaps that word - while it points on to "Quizzes", as the reviewed PNG
description says. The IS114 flowchart is a crop of its PNG without title and
footnotes (0.13), so it is described anew; Luna's 8 connections match the PNG's.
Owner's decisions, 17 Oct 2026: reuse; and Luna stays the model here, accepting
that it misses an unmarked crossing on p. 8 that stronger models find.

Rerunnable: earlier inserted descriptions are swapped back to placeholders
first; a figure already described is read from parsed/figures/ unless --redo.

    .venv/bin/python scripts/describe_pdf_figures.py [--redo]
"""
import json
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from diagram_connections import CHART_NOTE, ask, connections, keep_sections  # noqa: E402
from openai_client import VISION_MODEL as MODEL, client  # noqa: E402  (figures are images)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "eval set"
OUT = ROOT / "parsed"
FIG = OUT / "figures"

PDFS = [("MidtermSampleSolutions.pdf", "MidtermSampleSolutions__pdf"),
        ("IS114 AY26-T1 Course Content, Student Edition.pdf",
         "IS114_AY26-T1_Course_Content_Student_Edition__pdf")]
PLACEHOLDER = "<!-- image -->"
INSERTED = re.compile(r"\[Figure on p\. \d+ - Diagram description.*?\[End of figure \d+\]", re.S)
SCALE, PAD = 3, 6  # render at 3x (216 dpi); 6 pt margin around Docling's box

PROMPT_VERSION = "3"  # v2: arrows told a chart's lines are not connections; v3: every crossing
FIGURE_PROMPT = """\
You are writing part of a Diagram description: a text version of one picture
from a student's course material. It will be searched and quoted instead of
the picture, so anything it shows but you leave out can never be found.
Arrows are recorded separately - do not describe them.

Write Markdown with these sections:

## What it is
One or two sentences: the kind of picture (chart, graph, flowchart, table,
photo, ...) and its subject.

## All text, verbatim
Every piece of visible text, exactly as written, including axis labels, line
labels, point labels and tick values. Write a superscript as e.g. "P^*", a
subscript as e.g. "S_LR".

## What the chart shows
Only if it is a chart or graph:
- Axes: the label of each axis and every tick value printed on it.
- Every line or curve: its label (as printed) and colour; whether it rises or
  falls; where it starts and ends, and where it crosses an axis, using the
  printed tick values or labels (say "unlabelled" rather than guess a number).
- Every point where two lines or curves cross or meet, marked or not: which
  lines, and the axis labels or values it lines up with. Two lines may cross
  more than once - list each crossing.
- Every labelled point: its label, and which lines meet there; any dotted
  guide line from it to an axis, and the label it reaches.
- Every shaded or highlighted area or segment: its colour, its label, and
  which lines or points bound it.
Only state what is drawn or printed. Do not explain the subject.
"""
SECTIONS = ("What it is", "All text, verbatim", "What the chart shows")

# Library images a PDF picture may duplicate: (source file, its description).
LIBRARY_IMAGES = [
    ("IS114_eLearn_Quizzes_tab.png", "IS114_eLearn_Quizzes_tab__png.md"),
    ("IS114_Academic_Violation_Reporting_Procedure.png", "IS114_Academic_Violation_Reporting_Procedure__png.md"),
]
MATCH = 0.4


def _grey(im, side=48):
    a = np.asarray(im.convert("L").resize((side, side)), dtype=float)
    return a - a.mean()


def same_as_library_image(path):
    """(file, correlation) of the Library image this picture duplicates, or None."""
    a = _grey(Image.open(path))
    best = None
    for name, _ in LIBRARY_IMAGES:
        b = _grey(Image.open(SRC / name))
        r = float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
        if r >= MATCH and (best is None or r > best[1]):
            best = (name, r)
    return best


def crop(pdf_name, stem):
    """Crop every picture Docling found; returns [(png path, page)] in document order."""
    doc = json.loads((OUT / f"{stem}.json").read_text())
    pdf = pdfium.PdfDocument(SRC / pdf_name)
    FIG.mkdir(parents=True, exist_ok=True)
    out = []
    for i, pic in enumerate(doc["pictures"]):
        prov = pic["prov"][0]
        b, page = prov["bbox"], pdf[prov["page_no"] - 1]
        assert b["coord_origin"] == "BOTTOMLEFT", b
        h = page.get_height()
        box = (int((b["l"] - PAD) * SCALE), int((h - b["t"] - PAD) * SCALE),
               int((b["r"] + PAD) * SCALE), int((h - b["b"] + PAD) * SCALE))
        path = FIG / f"{stem.split('__')[0]}_p{prov['page_no']:02d}_{i:02d}.png"
        page.render(scale=SCALE).to_pil().crop(box).save(path)
        out.append((path, prov["page_no"]))
    return out


def describe(c, path, redo):
    md_path = path.with_suffix(".md")
    if match := same_as_library_image(path):
        name, r = match
        desc = dict(LIBRARY_IMAGES)[name]
        body = re.sub(r"^<!--.*?-->\s*", "", (OUT / desc).read_text(encoding="utf-8"), flags=re.S).strip()
        body = f"(Same picture as {name}; its reviewed description, reused.)\n\n" + body
        md_path.write_text(body + "\n", encoding="utf-8")
        path.with_suffix(".edges.json").unlink(missing_ok=True)
        return body, {"reused": name, "correlation": round(r, 2)}
    if md_path.exists() and not redo:
        return md_path.read_text(encoding="utf-8"), None
    text, usage = ask(c, MODEL, path, FIGURE_PROMPT)
    conn_md, audit, conn_usage = connections(c, MODEL, path, CHART_NOTE)
    body = (keep_sections(text, SECTIONS) + "\n" + conn_md).strip()
    md_path.write_text(body + "\n", encoding="utf-8")
    path.with_suffix(".edges.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    usages = [usage] + conn_usage
    return body, {"model": usage["model"], "calls": len(usages),
                  "prompt_tokens": sum(u["prompt_tokens"] for u in usages),
                  "completion_tokens": sum(u["completion_tokens"] for u in usages),
                  "agreed_connections": len(audit["agreed"]),
                  "unconfirmed_connections": len(audit["unconfirmed"])}


def as_inline(body, page, n):
    """The description as it sits in the PDF's text: no '## ' headings."""
    body = re.sub(r"(?m)^#{1,6}\s+(.*)$", r"**\1**", body)
    who = "reused" if body.startswith("(Same picture") else f"written by {MODEL}, prompt v{PROMPT_VERSION}"
    return (f"[Figure on p. {page} - Diagram description, {who}]\n\n"
            f"{body}\n\n[End of figure {n}]")


def insert(stem, figures):
    """Replace the placeholders, in order, in <stem>.md and <stem>.pages.md."""
    for name in (f"{stem}.md", f"{stem}.pages.md"):
        path = OUT / name
        md = path.read_text(encoding="utf-8")
        md = INSERTED.sub(PLACEHOLDER, md)  # undo an earlier run
        parts = md.split(PLACEHOLDER)
        if len(parts) - 1 != len(figures):
            sys.exit(f"{name}: {len(parts) - 1} placeholders but {len(figures)} pictures - not inserting")
        out = parts[0]
        for n, ((_, page, body), rest) in enumerate(zip(figures, parts[1:]), 1):
            out += as_inline(body, page, n) + rest
        path.write_text(out, encoding="utf-8")
        print(f"  {name}: {len(figures)} placeholders replaced")


def main():
    redo = "--redo" in sys.argv
    c = client()
    report = {"model": MODEL, "prompt_version": PROMPT_VERSION, "figures": {}}
    for pdf_name, stem in PDFS:
        crops = crop(pdf_name, stem)
        print(f"{pdf_name}: {len(crops)} pictures", flush=True)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda pp: describe(c, pp[0], redo), crops))
        figures = []
        for (path, page), (body, usage) in zip(crops, results):
            figures.append((path, page, body))
            report["figures"][path.name] = usage or "cached"
            print(f"  {path.name}: {usage or 'cached'}", flush=True)
        insert(stem, figures)
    (OUT / "figures_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
