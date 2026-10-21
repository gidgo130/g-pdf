# SPDX-License-Identifier: AGPL-3.0-or-later
# PROTOTYPE from the Colab "book prep" chat. Spec only; not imported by gpdf. See docs/book-pipeline-handoff.md
import re, os, shutil, difflib, collections, subprocess
from pypdf import PdfReader, PdfWriter

NUMW = "one two three four five six seven eight nine ten eleven twelve".split()
HEAD = re.compile(r"^[\W\d]{0,3}(chapter|chap\.?|part|book)\s+(\d+|[ivxlc]+|" + "|".join(NUMW) +
                  r")\b[\s:.\-–—]*(.{0,70})$", re.I)
SECTIONS = re.compile(r"^(foreword|preface|introduction|prologue|epilogue|contents|table of contents|"
                      r"acknowledg\w+|index|appendix|glossary|bibliography)\W*$", re.I)

def get_lines(reader):
    return [[l.strip() for l in (p.extract_text() or "").split("\n") if l.strip()] for p in reader.pages]

def has_text(reader, n=12):
    idx = sorted({int(i * (len(reader.pages) - 1) / max(n - 1, 1)) for i in range(n)})
    good = sum(len((reader.pages[i].extract_text() or "").strip()) >= 100 for i in idx)
    return good >= len(idx) / 2

def fix_title(t, num, toc):
    """toc: {chapter_number: title} parsed from the Contents page (may be empty)."""
    t = re.sub(r"\b([B-HJ-Z]) (?=[a-z]{2})", r"\1", t).strip(" .")          # "C hairman" -> "Chairman"
    sq = lambda x: re.sub(r"\W", "", x).lower()
    ref = toc.get(num)
    if ref:
        r = difflib.SequenceMatcher(None, sq(t), sq(ref)).ratio()
        if r >= 0.96 or r < 0.6: return ref       # spacing glitch, or OCR garbage -> trust the Contents page
    return t if len(t) > 3 else ""

def find_marks(lines, overrides):
    marks, seen, last_pg = [], set(), 0
    toc = {}
    for L in lines[:20]:
        if any(re.fullmatch(r"(table of )?contents\W*", l, re.I) for l in L[:4]):
            for l in L:
                m = re.match(r"^(\d+)\.\s*(.+?)[\s.~:|]*\d*$", l)      # "3. Orphans in Uniform . 16"
                if m and re.search(r"[A-Za-z]{3}", m.group(2)): toc.setdefault(m.group(1), m.group(2))
    for i, L in enumerate(lines, 1):
        if i <= 30:
            for l in L[:3]:
                if SECTIONS.match(l): marks.append((l.strip(" .").title(), i)); break
        if any(re.fullmatch(r"(table of )?contents\W*", l, re.I) for l in L[:4]): continue   # skip TOC pages for chapters
        for j, l in enumerate(L[:6]):
            m = HEAD.match(l)
            if not m or re.search(r"\.{3}|\d$", m.group(3) or ""): continue
            kind = m.group(1).lower()[:4]; kind = "part" if kind in ("part", "book") else "ch"
            num = m.group(2).upper()
            if (kind, num) in seen or i < last_pg: break
            title = (m.group(3) or "").strip() or next((x for x in L[j+1:j+3] if len(x) > 3), "")
            label = f"{num}. " if kind == "ch" else f"Part {num}: "
            marks.append(((label + fix_title(title, num, toc)).strip(" .:"), i))
            seen.add((kind, num)); last_pg = i; break
    for n, pg in overrides.items():                      # manual fixes: {16: 198}
        marks = [x for x in marks if not x[0].startswith(f"{n}. ")]
        marks.append((f"{n}. ", pg))
    return sorted(marks, key=lambda x: x[1])

def find_offset(lines):
    offs = []
    for i, L in enumerate(lines, 1):
        for l in L[:2] + L[-2:]:
            m = (re.match(r"^(\d{1,3})\s+[A-Za-z]", l) or re.search(r"[A-Za-z.?!]\s+(\d{1,3})$", l)
                 or re.fullmatch(r"(\d{1,3})", l))
            if m: offs.append(i - int(m.group(1))); break
    c = collections.Counter(offs).most_common(1)
    return c[0][0] if c and c[0][1] >= 5 else None       # need agreement from 5+ pages

def process(src, lang="eng", pages=None, title="", author="", overrides=None, compress=True, jobs=2):
    stem = os.path.splitext(os.path.basename(src))[0]
    r = PdfReader(src)
    scanned = not has_text(r)
    print(f"{len(r.pages)} pages; text layer: {'NO -> running OCR' if scanned else 'yes -> skipping OCR'}")
    ocr = f"{stem}_ocr.pdf"
    if scanned:
        cmd = ["ocrmypdf", "-l", lang, "--rotate-pages", "--skip-text", "--optimize", "1",
               "--output-type", "pdf", "--jobs", str(jobs)] + (["--pages", pages] if pages else []) + [src, ocr]
        subprocess.run(cmd, check=True)
    else:
        shutil.copy(src, ocr)
    r = PdfReader(ocr); lines = get_lines(r)
    marks, offset = find_marks(lines, overrides or {}), find_offset(lines)

    print(f"\nPrinted page = PDF page - {offset}" if offset is not None else "\nPrinted-page offset: not detected")
    for t, pg in marks: print(f"{pg:>4}  {t}")
    nums = sorted(int(m[0].split('.')[0]) for m in marks if re.match(r"\d+\. ", m[0]))
    gaps = [n for n in range(1, (nums[-1] if nums else 0) + 1) if n not in nums]
    print("Missing chapter numbers:", gaps or "none")
    empty = [i for i, L in enumerate(lines, 1) if sum(map(len, L)) < 20]
    print(f"Pages with almost no text (plates/blank/OCR failure): {empty[:20]}{'...' if len(empty) > 20 else ''}")

    meta = r.metadata or {}
    w = PdfWriter(clone_from=r)
    w.add_metadata({"/Title": title or meta.get("/Title") or stem.replace("_", " "),
                    "/Author": author or meta.get("/Author") or ""})
    for t, pg in marks: w.add_outline_item(t, pg - 1)
    if marks: w.page_mode = "/UseOutlines"
    final = f"{stem}_processed.pdf"; tmp = f"{stem}_tmp.pdf"
    w.write(tmp)
    if compress and scanned:
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
            "-dDownsampleMonoImages=true", "-dMonoImageResolution=300", "-dMonoImageDownsampleType=/Subsample",
            "-dMonoImageDownsampleThreshold=1.0", "-dDownsampleGrayImages=true", "-dGrayImageResolution=300",
            f"-sOutputFile={final}", tmp], check=True)
        if os.path.getsize(final) >= os.path.getsize(tmp): shutil.move(tmp, final)
    else:
        shutil.move(tmp, final)
    print(f"\n{os.path.getsize(src)/1e6:.1f} MB -> {os.path.getsize(final)/1e6:.1f} MB  ({final})")

    txt = f"{stem}_pages.txt"
    with open(txt, "w", encoding="utf-8") as f:
        for i, L in enumerate(lines, 1):
            tag = f"[[PDF p. {i} | printed p. {i - offset if offset is not None else '?'}]]"
            for t, pg in marks:
                if pg == i: tag = f"\n===== {t} =====\n" + tag
            f.write(f"\n{tag}\n" + "\n".join(L) + "\n")
    return final, txt
