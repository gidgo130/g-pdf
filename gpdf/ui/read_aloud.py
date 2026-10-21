# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline read-aloud using the system's own speech voices (QtTextToSpeech).

Nothing here talks to the network or an AI service. Windows uses the voices
installed under Settings > Time & language > Speech; a language with no
installed voice (e.g. Spanish) can't be read until you add that voice pack.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:  # module missing in some installs
    QTextToSpeech = None  # type: ignore[assignment]

from gpdf.core.document import PdfDocument
from gpdf.core.speech_text import page_chunks

NO_VOICES_HINT = (
    "No speech voices found. On Windows, add voices under Settings > Time & "
    "language > Speech."
)


class ReadAloudController(QObject):
    """Reads a document sentence by sentence, page after page."""

    state_changed = Signal(str)       # "stopped" | "playing" | "paused"
    page_started = Signal(int)

    def __init__(self, engine: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tts = None
        if QTextToSpeech is not None and QTextToSpeech.availableEngines():
            self._tts = QTextToSpeech(engine) if engine else QTextToSpeech()
            self._tts.stateChanged.connect(self._on_tts_state)
        self._doc: PdfDocument | None = None
        self._page = 0
        self._chunks: list[str] = []
        self._pos = 0
        self._state = "stopped"
        self._advancing = False

    @property
    def available(self) -> bool:
        return self._tts is not None and bool(self._tts.availableVoices())

    @property
    def state(self) -> str:
        return self._state

    @property
    def page(self) -> int:
        return self._page

    def voices(self) -> list[tuple[str, object]]:
        if self._tts is None:
            return []
        return [(f"{v.name()} ({v.locale().name()})", v) for v in self._tts.availableVoices()]

    def set_voice(self, voice) -> None:
        if self._tts is not None:
            self._tts.setVoice(voice)

    def set_rate(self, rate: float) -> None:
        """-1.0 (slow) .. 1.0 (fast)."""
        if self._tts is not None:
            self._tts.setRate(max(-1.0, min(1.0, rate)))

    def set_document(self, doc: PdfDocument | None) -> None:
        self.stop()
        self._doc = doc

    # -- transport -------------------------------------------------------------

    def play(self, start_page: int | None = None) -> None:
        if self._tts is None or self._doc is None:
            return
        if self._state == "paused" and start_page is None:
            self._tts.resume()
            self._set_state("playing")
            return
        self._stop_engine()
        self._page = start_page if start_page is not None else self._page
        self._load_page(self._page)
        self._set_state("playing")
        self._speak_next()

    def pause(self) -> None:
        if self._state == "playing" and self._tts is not None:
            self._tts.pause()
            self._set_state("paused")

    def stop(self) -> None:
        self._stop_engine()
        self._set_state("stopped")

    def _stop_engine(self) -> None:
        if self._tts is not None:
            self._advancing = True  # ignore the Ready state that stop() causes
            try:
                self._tts.stop()
            finally:
                self._advancing = False
        self._chunks = []
        self._pos = 0

    # -- internals ---------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _load_page(self, page: int) -> None:
        assert self._doc is not None
        self._page = page
        self._chunks = page_chunks(self._doc, page)
        self._pos = 0
        self.page_started.emit(page)

    def _speak_next(self) -> None:
        assert self._tts is not None and self._doc is not None
        while self._pos >= len(self._chunks):
            if self._page + 1 >= self._doc.page_count:
                self.stop()
                return
            self._load_page(self._page + 1)
        chunk = self._chunks[self._pos]
        self._pos += 1
        self._advancing = True
        try:
            self._tts.say(chunk)
        finally:
            self._advancing = False

    def _on_tts_state(self, state) -> None:
        if self._advancing or self._state != "playing":
            return
        if QTextToSpeech is not None and state == QTextToSpeech.State.Ready:
            self._speak_next()
        elif QTextToSpeech is not None and state == QTextToSpeech.State.Error:
            self.stop()


class ReadAloudBar(QWidget):
    """Compact transport bar. Starts from the page the view is on."""

    def __init__(self, controller: ReadAloudController, get_current_page, go_to_page,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._get_current_page = get_current_page
        self._go_to_page = go_to_page

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        self.play_btn = QPushButton("▶ Read aloud")
        self.stop_btn = QPushButton("■ Stop")
        self.voice = QComboBox()
        self.voice.setMinimumContentsLength(18)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setFixedWidth(100)
        self.rate.setToolTip("Speed")
        self.message = QLabel()
        for w in (self.play_btn, self.stop_btn, self.voice, QLabel("Speed"), self.rate, self.message):
            row.addWidget(w)
        row.addStretch(1)

        if controller.available:
            for label, voice in controller.voices():
                self.voice.addItem(label, voice)
        else:
            self.play_btn.setEnabled(False)
            self.voice.setEnabled(False)
            self.rate.setEnabled(False)
            self.message.setText(NO_VOICES_HINT)
        self.stop_btn.setEnabled(False)

        self.play_btn.clicked.connect(self._toggle)
        self.stop_btn.clicked.connect(controller.stop)
        self.voice.currentIndexChanged.connect(
            lambda _: controller.set_voice(self.voice.currentData()))
        self.rate.valueChanged.connect(lambda v: controller.set_rate(v / 10))
        controller.state_changed.connect(self._on_state)
        controller.page_started.connect(self._on_page)

    def _toggle(self) -> None:
        c = self.controller
        if c.state == "playing":
            c.pause()
        elif c.state == "paused":
            c.play()
        else:
            c.play(self._get_current_page())

    def _on_state(self, state: str) -> None:
        self.play_btn.setText({"playing": "❚❚ Pause", "paused": "▶ Resume"}.get(state, "▶ Read aloud"))
        self.stop_btn.setEnabled(state != "stopped")
        if state == "stopped":
            self.message.clear()

    def _on_page(self, page: int) -> None:
        self.message.setText(f"Reading page {page + 1}")
        self._go_to_page(page)
