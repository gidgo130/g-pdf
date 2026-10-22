# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiny synthetic corpus for the survey tests, generated at test time (never real
corpus files): seeded prose made of common words (so 5-word shingles differ between
seeds), OCR-style noise, born-digital PDFs, 300 dpi 1-bit scans with or without an
invisible text layer, and .txt files."""

from __future__ import annotations

import numpy as np
import pymupdf

from . import synth

ES_WORDS = """
el la los las un una de del en con por para sobre entre desde hasta sin según
casa ciudad tiempo vida mundo hombre mujer noche día año obra poeta poesía arte
libro palabra lengua historia país pueblo tierra mar cielo sol luz sombra camino
calle puerta ventana mesa carta revista periódico manifiesto grupo generación
crítica lector escritor pintor música color forma idea sueño memoria voz silencio
nuevo viejo joven grande pequeño largo breve oscuro claro alto bajo moderno antiguo
primero último propio otro mismo cierto mucho poco todo nada siempre nunca ahora
luego después antes también tampoco todavía apenas casi muy más menos bien mal
escribe lee mira dice piensa busca encuentra pierde sabe quiere puede debe tiene
hace vive muere nace llega sale entra vuelve sigue cambia rompe funda publica
""".split()
EN_WORDS = """
the a of in on with for from about between without after before during under
house city time life world man woman night day year work poet poetry art book
word language history country people land sea sky sun light shadow road street
door window table letter magazine journal manifesto group generation criticism
reader writer painter music color form idea dream memory voice silence new old
young great small long short dark clear high low modern ancient first last own
other same certain much little all nothing always never now then later also still
writes reads looks says thinks seeks finds loses knows wants can must has makes
lives dies arrives leaves enters returns follows changes breaks founds publishes
""".split()
CA_WORDS = """
el la els les un una de del amb per sobre entre fins sense segons casa ciutat temps
vida món home dona nit dia any obra poeta poesia art llibre paraula llengua història
país poble terra mar cel llum ombra camí carrer porta finestra taula carta revista
grup generació crítica lector escriptor pintor música color forma idea somni memòria
veu silenci nou vell jove gran petit llarg breu fosc clar alt baix modern antic primer
últim propi altre mateix molt poc tot res sempre mai ara després abans també encara
escriu llegeix mira diu pensa busca troba perd sap vol pot ha de té fa viu mor neix
""".split()
WORDS = {"es": ES_WORDS, "en": EN_WORDS, "ca": CA_WORDS}


def prose(seed: int, n: int, lang: str = "es", line: int = 11) -> str:
    """``n`` words in lines of ``line`` words, seeded."""
    rng = np.random.default_rng(seed)
    pool = WORDS[lang]
    words = [pool[i] for i in rng.integers(0, len(pool), n)]
    return "\n".join(" ".join(words[i:i + line]) for i in range(0, n, line))


_SWAPS = [("m", "rn"), ("e", "c"), ("o", "0"), ("a", "4"), ("i", "1"), ("u", "ii"),
          ("n", "ri"), ("l", "1")]


def noisy(text: str, rate: float, seed: int = 0) -> str:
    """OCR-like damage on about ``rate`` of the words (letters inside words misread)."""
    rng = np.random.default_rng(seed)
    out = []
    for ln in text.split("\n"):
        words = []
        for w in ln.split(" "):
            if len(w) >= 3 and rng.random() < rate:
                for a, b in _SWAPS:
                    k = w.find(a, 1, len(w) - 1)
                    if k > 0:
                        w = w[:k] + b + w[k + 1:]
                        break
                else:
                    w = w[:1] + "x" + w[2:]
            words.append(w)
        out.append(" ".join(words))
    return "\n".join(out)


def born_digital(path, pages: list[str], title="", author="", toc=None, blank_pages=()):
    """A PDF with visible text; ``blank_pages`` (1-based) get no text and no image."""
    doc = pymupdf.open()
    for i, text in enumerate(pages):
        page = doc.new_page(width=420, height=600)
        if i + 1 not in blank_pages:
            rc = page.insert_textbox(pymupdf.Rect(30, 30, 390, 570), text, fontname="tiro",
                                     fontsize=9)
            assert rc >= 0, "text overflows the page"
    doc.set_metadata({"title": title, "author": author, "producer": "corpus.py"})
    if toc:
        doc.set_toc(toc)
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()


def scan_page(doc, image_text: str | None, layer: str | None = None, size=11.0):
    """A 300 dpi 1-bit (G4) scan page of ``image_text`` (None: white paper) with an
    invisible text layer ``layer`` (None: no layer)."""
    w, h = synth.OCR_W, synth.OCR_H
    if image_text is None:
        black = np.zeros((round(h / synth.OCR_PT), round(w / synth.OCR_PT)), bool)
    else:
        dark, *_ = synth.typeset(image_text.split("\n"), size)
        black = synth.to_black(dark)
    page = doc.new_page(width=w, height=h)
    synth.put_image(page, synth.g4(doc, black))
    if layer:
        rc = page.insert_textbox(pymupdf.Rect(36, 40, w - 36, h - 40), layer, fontname="tiro",
                                 fontsize=size, render_mode=3)
        assert rc >= 0, "layer overflows the page"
    return page


def picture_page(doc):
    """A scan of a picture: dark shapes, no text."""
    w, h = synth.OCR_W, synth.OCR_H
    hh, ww = round(h / synth.OCR_PT), round(w / synth.OCR_PT)
    yy, xx = np.mgrid[0:hh, 0:ww]
    black = ((xx - ww / 2) ** 2 + (yy - hh / 2) ** 2 < (ww / 3) ** 2) & ((xx // 40 + yy // 40) % 2 == 0)
    page = doc.new_page(width=w, height=h)
    synth.put_image(page, synth.g4(doc, black))
    return page


def scan(path, pages, title=""):
    """``pages``: (image_text | None | "picture", layer | None) per page."""
    doc = pymupdf.open()
    for image_text, layer in pages:
        if image_text == "picture":
            picture_page(doc)
        else:
            scan_page(doc, image_text, layer)
    doc.set_metadata({"title": title, "producer": "corpus.py scan"})
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()


def txt(path, text: str, encoding="utf-8"):
    path.write_bytes(text.encode(encoding))
