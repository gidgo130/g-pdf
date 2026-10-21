# Manual tests

Automated tests cover the tab/window logic and the drag gesture up to the moment
a real drag would start, but they can't simulate an actual mouse drag. Run these
by hand on Windows before a release. Use three or four small PDFs.

## Tab dragging

1. Open three PDFs. Drag the middle tab **down about 50 px** and release over
   empty desktop. A new window appears near the cursor holding that tab; the
   original window keeps the other two.
2. Drag the new window's tab back onto the **first window's tab strip**, between
   two tabs. It lands where you dropped it, and the now-empty window closes.
3. Detach a tab again and drop it on the **page area** of the other window. It
   is appended as the last tab.
4. Drag a tab left and right **within the strip**. It reorders smoothly, with no
   detach.
5. Open a window with **one tab** and drag it out. The window effectively moves;
   no empty window is left behind.
6. Drag a tab onto the **menu bar or status bar** of another g-pdf window. It
   detaches into a new window (those areas don't accept tabs).
7. Press **Esc** mid-drag. *Known limitation:* it detaches rather than
   cancelling.

## Files

8. Drag a `.pdf` from Explorer onto the tab strip, the page area, and the empty
   start screen. Each opens a tab. A non-PDF is ignored.
9. Open the same file twice (also from a second window). The existing tab is
   focused; no duplicate appears.
10. Open a corrupt or password-protected PDF. A clear message appears and no tab
    is added.

## Viewing

11. Ctrl+wheel, Ctrl+`=`, Ctrl+`-`, Ctrl+0 (fit width), Ctrl+1 (100%). The page
    at the top of the view stays put while zooming.
12. Resize the window while on fit-width: the page re-fits. After a manual zoom,
    resizing does not change the zoom.
13. Scroll a 100+ page PDF quickly. No long freezes (rendering is synchronous
    for now, so note any that you see).
14. On a 125% or 150% display, text is sharp. Drag a tab between monitors with
    different scaling; it re-renders sharp.

## Page panel (new in 0.2)

15. Click a thumbnail: the view jumps to that page. Ctrl/Shift-click selects several.
16. Drag a selected thumbnail between two others: a blue line shows the drop spot;
    on release the pages move and stay selected. Ctrl+Z puts them back.
17. Drag thumbnails onto another g-pdf window's panel (detach a tab first): the
    pages are *copied* there; the source is unchanged.
18. Drag a `.pdf` from Explorer onto the panel: its pages are inserted at the line.
19. Rotate, delete, duplicate, insert blank, insert from file, extract. Each is one
    undo step. Deleting every page is refused with a message.
20. Edit something, then close the tab/window: Save / Discard / Cancel prompt.
    Cancel keeps everything open. Save writes the file; tab loses the ● marker.
21. Open a PDF in another program (e.g. a browser) while it is open here: saving
    from g-pdf must still work (the file is not locked).

## Content tools, find, read aloud (new in 0.2)

22. Tools > Edit Text, click a line, change a word. Compare the font with
    neighbouring text (it is substituted: Helvetica/Times/Courier).
23. Edit Text on a *rotated* page and on a scan: a clear message, no change.
24. Add Text and Add Image (drag an area, pick a PNG/JPG). Undo each.
25. Ctrl+F: matches highlight; Enter / arrows step through them; Esc closes. Edit a
    page and watch the match count update.
26. Read Aloud (Ctrl+Shift+R): play, pause, resume, stop, change voice and speed.
    It continues onto following pages and scrolls along. Spanish text needs a
    Spanish voice installed.
27. Properties: edit the title; "Remove personal info" then OK; reopen the dialog
    and check author/keywords are empty. Undo restores them.
28. OCR a scanned page with Tesseract installed; afterwards Ctrl+F finds its words.
    Without Tesseract you should see the install hint.
29. Open a signed PDF and save: a warning explains the signature will be invalidated.
