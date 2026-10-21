# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from PySide6.QtCore import QTimer

from gpdf.core.document import PdfDocument
from gpdf.ui.read_aloud import ReadAloudController


@pytest.fixture
def doc(make_pdf, qapp):
    d = PdfDocument(make_pdf("r.pdf", sizes=[(600, 400)] * 3))
    yield d
    d.close()


def test_reads_all_pages_then_stops(doc, qapp):
    c = ReadAloudController(engine="mock")
    assert c.available
    c.set_document(doc)
    pages, states = [], []
    c.page_started.connect(pages.append)
    c.state_changed.connect(states.append)
    c.play(0)
    for _ in range(300):
        qapp.processEvents()
        if c.state == "stopped":
            break
        QTimer.singleShot(10, lambda: None)
        qapp.processEvents()
        import time; time.sleep(0.01)
    assert pages[:3] == [0, 1, 2] and states[0] == "playing" and states[-1] == "stopped"


def test_pause_resume_stop(doc, qapp):
    c = ReadAloudController(engine="mock")
    c.set_document(doc)
    c.play(1)
    assert c.state == "playing" and c.page == 1
    c.pause()
    assert c.state == "paused"
    c.play()
    assert c.state == "playing"
    c.stop()
    assert c.state == "stopped"


def test_no_engine_is_safe(doc, qapp, monkeypatch):
    import gpdf.ui.read_aloud as ra
    monkeypatch.setattr(ra, "QTextToSpeech", None)
    c = ReadAloudController()
    assert not c.available
    c.set_document(doc)
    c.play(0)
    assert c.state == "stopped"
