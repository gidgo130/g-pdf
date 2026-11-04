# SPDX-License-Identifier: AGPL-3.0-or-later
"""Math OCR: a PDF of a mathematical book or paper into per-page Markdown + LaTeX (the
distribution-atlas page format), a review queue of the formulas worth a second look, and
a scorer against a reference transcription.

The engine (MinerU, run as an external program the user installs) is never imported or
bundled. No Qt here, like the rest of ``gpdf.pipeline``. The identity checks need the
optional ``mathcheck`` extra (sympy, mpmath, antlr4); everything else is standard library
plus PyMuPDF.
"""

MATHCHECK_HINT = 'identity checks need the mathcheck extra: pip install "g-pdf[mathcheck]"'
