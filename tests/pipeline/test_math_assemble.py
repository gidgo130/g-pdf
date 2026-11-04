# SPDX-License-Identifier: AGPL-3.0-or-later
"""Page assembly in the atlas format from hand-written engine blocks: heading levels,
\\tag, inline delimiters, an HTML table, a figure, footnotes, a split display, a banner,
running heads, balance, the page marker and its flags."""

import json

from gpdf.pipeline.mathocr import assemble, latex
from gpdf.pipeline.mathocr.labels import Label


def T(text, y=0.3, kind="text", **extra):
    b = {"type": kind, "bbox": [0.1, y, 0.9, y + 0.05],
         "content": [{"type": "text", "content": text}]}
    b.update(extra)
    return b


def P(*parts, y=0.3):
    """A paragraph of text and inline-math parts: strings and ("eq", latex)."""
    content = [{"type": "equation_inline", "content": p[1]} if isinstance(p, tuple)
               else {"type": "text", "content": p} for p in parts]
    return {"type": "text", "bbox": [0.1, y, 0.9, y + 0.05], "content": content}


def EQ(latex_, y=0.5):
    return {"type": "equation", "bbox": [0.2, y, 0.8, y + 0.06], "content": latex_,
            "image_path": "images/page_0_equation_3.jpg"}


def H(text, level=2, y=0.2, kind="paragraph_title"):
    return T(text, y, kind, level=level)


def page(blocks, file=73, label=Label("54", None)):
    return assemble.assemble(blocks, file, label)


def body(pg):
    """The page text after the marker line."""
    md = pg.render()
    assert md.startswith(f'<!-- page printed="{pg.label.marker}" file="{pg.file}" -->\n')
    rest = md.split("\n", 1)[1]
    assert rest.startswith("\n")              # a blank line after the marker
    return rest[1:]


def balanced(md):
    return md.count("$$") % 2 == 0 and md.count("\\(") == md.count("\\)")


# ---------------------------------------------------------------- headings

def test_heading_levels_follow_the_printed_numbering():
    pg = page([T("CHAPTER 48", 0.1), H("Multivariate Gamma Distributions", 1, 0.2, "doc_title"),
               H("1 INTRODUCTION", 2, 0.3), T("Body.", 0.35), H("2.4 Moments", 2, 0.4),
               H("2.4.1 First", 2, 0.5), H("Unnumbered", 2, 0.6), H("Deep", 3, 0.7)])
    assert body(pg).rstrip("\n").split("\n\n") == [
        "# 48 Multivariate Gamma Distributions", "## 1 INTRODUCTION", "Body.", "### 2.4 Moments",
        "#### 2.4.1 First", "## Unnumbered", "### Deep"]


def test_a_doc_title_alone_is_a_chapter_heading_and_section_style_numbers_keep_their_dot():
    pg = page([H("Some Title", 1, 0.2, "doc_title"), H("1. INTRODUCTION", 2, 0.3)])
    assert body(pg).startswith("# Some Title\n\n## 1. INTRODUCTION")


# ---------------------------------------------------------------- math

def test_displays_get_their_tag_on_its_own_line_and_inline_math_its_delimiters():
    pg = page([P("These constraints are", y=0.2),
               EQ(r"1 + \sum_ {i} a _ {i} \geq 0,\tag {44.74}", 0.3),
               P("where  ", ("eq", r"\varepsilon_{i} = \pm1"), " . For this form", y=0.4),
               EQ(r"F(x) = 1 - e^{-x} \qquad (44.75)", 0.5),
               EQ(r"\Pr[X = 0] = p", 0.6)])
    assert body(pg) == (
        "These constraints are\n\n"
        "$$\n1 + \\sum_ {i} a _ {i} \\geq 0,\n\\tag{44.74}\n$$\n\n"
        "where \\(\\varepsilon_{i} = \\pm1\\). For this form\n\n"
        "$$\nF(x) = 1 - e^{-x}\n\\tag{44.75}\n$$\n\n"
        "$$\n\\Pr[X = 0] = p\n$$\n")
    assert [(d.latex, d.tag) for d in pg.displays] == [
        (r"1 + \sum_ {i} a _ {i} \geq 0,", "44.74"), (r"F(x) = 1 - e^{-x}", "44.75"),
        (r"\Pr[X = 0] = p", None)]
    assert pg.displays[0].bbox == [0.2, 0.3, 0.8, 0.36]
    assert [p.inlines for p in pg.paragraphs] == [[], [r"\varepsilon_{i} = \pm1"]]
    displays, inline, _ = latex.extract(pg.render())
    assert len(displays) == 3 and inline == [r"\varepsilon_{i} = \pm1"]
    assert pg.flags[-1] == {"type": "summary", "display_formulas": 3,
                            "source": "mineru-standard"}


def test_engine_math_cannot_unbalance_the_delimiters_either():
    # seen from the real engine: a \( inside the tag, a $ inside a formula
    pg = page([EQ(r"f(x) = e^{-x}, \quad x > 0,\tag{\((1.1)\}", 0.3),
               EQ(r"g(x) = $y$ + \(z\)", 0.5), P("and ", ("eq", r"a $b$ \(c\)"), y=0.6)])
    md = pg.render()
    assert balanced(md)
    assert [(d.latex, d.tag) for d in pg.displays] == [(r"f(x) = e^{-x}, \quad x > 0,", "1.1"),
                                                       ("g(x) = y + (z)", None)]
    assert pg.paragraphs[0].inlines == ["a b (c)"]
    pg = page([EQ(r"h(x) = 1 \tag{junk}", 0.3)])
    assert pg.displays[0].tag is None and "junk" not in pg.render()


def test_prose_cannot_unbalance_the_math_delimiters():
    pg = page([P("Costs $5 and \\(not math\\) here", ("eq", "x"), "end.")])
    md = pg.render()
    assert balanced(md)
    assert "Costs \\$5 and (not math) here \\(x\\) end." in md


# ---------------------------------------------------------------- tables, figures, footnotes

def table(html, y=0.5):
    return {"type": "table", "bbox": [0.2, y, 0.8, y + 0.1], "content": [
        T("TABLE 45.4", y - 0.04, "table_caption"),
        {"type": "table_body", "bbox": [0.2, y, 0.8, y + 0.1], "content": html},
        T("Source: Drezner (1990).", y + 0.11, "table_footnote")]}


def test_a_simple_html_table_becomes_markdown():
    html = ("<table><tr><td><eq>\\rho</eq></td><td>Ave. error</td></tr>"
            "<tr><td>0.1</td><td>0.00003</td></tr><tr><td>0.2</td><td>0.00012</td></tr></table>")
    pg = page([table(html)])
    assert body(pg) == ("TABLE 45.4\n\n| \\(\\rho\\) | Ave. error |\n|---|---|\n"
                        "| 0.1 | 0.00003 |\n| 0.2 | 0.00012 |\n\nSource: Drezner (1990).\n")
    assert not [f for f in pg.flags if f["type"] == "table_unstructured"]


def test_a_table_with_spans_stays_html_and_is_flagged():
    html = ('<table><tr><td rowspan="2">r</td><td colspan="2">Appr.</td></tr>'
            "<tr><td>Ave.</td><td>Max.</td></tr><tr><td>0.1</td><td>1</td><td>2</td></tr></table>")
    pg = page([table(html)])
    assert "<table>" in body(pg) and 'rowspan="2"' in body(pg)
    assert {"type": "table_unstructured", "note": "rowspan/colspan: kept as HTML"} in pg.flags


def figure(caption, y=0.5):
    content = [{"type": "image_body", "bbox": [0.2, y, 0.8, y + 0.2], "image_path": "images/x.jpg"}]
    if caption is not None:
        content.append(T(caption, y + 0.21, "image_caption"))
    return {"type": "image", "bbox": [0.2, y, 0.8, y + 0.25], "content": content}


def test_figures_and_footnotes():
    pg = page([T("Text with a note.", 0.2), figure("Figure 45.2 Contours of the density."),
               figure("FIGURE 3. Another."), figure(None, 0.8),
               T("1 The note text.", 0.95, "footnote"), T("† A second note.", 0.97, "footnote")])
    assert body(pg) == ("Text with a note.\n\n[Figure 45.2: Contours of the density.]\n\n"
                        "[Figure 3: Another.]\n\n[Figure: no caption]\n\n"
                        "[^1]: The note text.\n\n[^†]: A second note.\n")


def test_lists_and_unknown_blocks_are_text():
    pg = page([{"type": "list", "bbox": [0.1, 0.2, 0.9, 0.3], "content": [
        T("first item", 0.2), T("second item", 0.25)]},
        {"type": "code", "bbox": [0.1, 0.4, 0.9, 0.5], "content": "x = 1"}])
    assert body(pg) == "- first item\n- second item\n\nx = 1\n"


# ---------------------------------------------------------------- furniture

def test_running_heads_are_dropped_and_recorded_page_numbers_silently():
    pg = page([{"type": "page_number", "bbox": [0.09, 0.06, 0.12, 0.07],
                "content": [{"type": "text", "content": "54"}]},
               T("MULTIVARIATE SYSTEMS", 0.06, "header"), T("Body.", 0.3),
               T("Vol. 1 footer", 0.95, "footer")])
    assert body(pg) == "Body.\n"
    assert [f for f in pg.flags if f["type"] == "dropped"] == [
        {"type": "dropped", "text": "MULTIVARIATE SYSTEMS", "what": "running_head"},
        {"type": "dropped", "text": "Vol. 1 footer", "what": "running_foot"}]


def test_a_banner_repeated_on_run_openers_is_dropped_only_there():
    banner = "Some Book, Volume 1. By A. Author. Copyright 2000 Publisher. ISBN 0-000"
    pages = [page([T(banner, 0.1), T("CHAPTER 1", 0.2), H("First", 1, 0.3, "doc_title"),
                   T("Text one.", 0.5)], file=10),
             page([T(banner, 0.1), T("Text two.", 0.5)], file=11),
             page([T(banner, 0.1), T("CHAPTER 2", 0.2), H("Second", 1, 0.3, "doc_title"),
                   T("Text three.", 0.5)], file=30)]
    assemble.drop_banners(pages, openers={10, 30})
    assert body(pages[0]) == "# 1 First\n\nText one.\n"
    assert body(pages[2]) == "# 2 Second\n\nText three.\n"
    assert banner in body(pages[1])                      # not an opener: kept
    assert {"type": "dropped", "text": banner, "what": "banner"} in pages[0].flags
    assert {"type": "dropped", "text": banner, "what": "banner"} in pages[2].flags
    assert not [f for f in pages[1].flags if f["type"] == "dropped"]


def test_a_banner_needs_two_openers():
    banner = "Some Book, Volume 1. By A. Author. Copyright 2000 Publisher. ISBN 0-000"
    pages = [page([T(banner, 0.1), T("Text one.", 0.5)], file=10),
             page([T("Text two.", 0.5)], file=30)]
    assemble.drop_banners(pages, openers={10, 30})
    assert banner in body(pages[0])


# ---------------------------------------------------------------- page boundaries

def test_a_split_display_is_closed_reopened_and_flagged_on_both_pages():
    a = page([T("the joint density function", 0.8), EQ(r"p(x) = \exp\{-(a_0 + a_1 x_1", 0.9)],
             file=310, label=Label("292", None))
    b = page([EQ(r"+ a_2 x_2)\} \tag{46.104}", 0.1), T("has the normal form.", 0.2)],
             file=311, label=Label("293", None))
    c = page([EQ(r"y = 1 \tag{46.105}", 0.9)], file=312, label=Label("294", None))
    d = page([EQ(r"z = 2", 0.1)], file=313, label=Label("295", None))
    assemble.link_split_displays([a, b, c, d])
    assert a.flags[0] == {"type": "page_anomaly", "what": "split_display",
                          "note": "the last display may continue on file page 311 "
                                  "(closed here, reopened there)"}
    assert b.flags[0] == {"type": "page_anomaly", "what": "split_display",
                          "note": "display may continue from file page 310; $$ reopened at the "
                                  "top of this page"}
    assert b.render().startswith('<!-- page printed="293" file="311" -->\n$$\n+ a_2 x_2)\\}\n')
    assert not [f for f in c.flags + d.flags if f["type"] == "page_anomaly"]   # c's is tagged
    assert all(balanced(p.render()) for p in (a, b, c, d))


def test_a_page_starting_mid_paragraph_continues_right_after_the_marker():
    pg = page([T("function in each of the variables.", 0.1), T("Next paragraph.", 0.2)])
    assert pg.render() == ('<!-- page printed="54" file="73" -->\n'
                           "function in each of the variables.\n\nNext paragraph.\n")
    pg = page([T("Function in each of the variables.", 0.1)])
    assert pg.render().split("\n")[1] == ""


def test_label_flags_and_the_marker():
    pg = page([T("Body.", 0.3)], file=63, label=Label(None, "44"))
    assert pg.render().startswith('<!-- page printed="" file="63" -->')
    assert {"type": "label_inferred", "value": "44"} in pg.flags
    pg = page([T("Body.", 0.3)], file=64, label=Label(None, None))
    assert {"type": "label_missing"} in pg.flags
    assert json.loads(pg.flags_json())[0] == {"type": "label_missing"}
