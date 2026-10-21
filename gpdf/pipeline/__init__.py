# SPDX-License-Identifier: AGPL-3.0-or-later
"""Book-prep pipeline: batch work on whole PDFs that writes new files.

No Qt here (and no gpdf.core / gpdf.ui, which import Qt): the CLI and tests use this
package without a display. Pixel work needs the optional ``prep`` extra (numpy, Pillow),
imported by the modules that need it, never by this package itself.
"""

PREP_HINT = 'this needs the book-prep extra: pip install "g-pdf[prep]"'
