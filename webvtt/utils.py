"""Utils module."""

import locale
import os
import re
import codecs
from collections.abc import Generator, Iterable

CODEC_BOMS = {
    'utf-8': codecs.BOM_UTF8,
    'utf-32-le': codecs.BOM_UTF32_LE,
    'utf-32-be': codecs.BOM_UTF32_BE,
    'utf-16-le': codecs.BOM_UTF16_LE,
    'utf-16-be': codecs.BOM_UTF16_BE
}

# the codecs that always write a Byte Order Mark
_BOM_WRITING_CODECS = ('utf-8-sig', 'utf-16', 'utf-32')

# the line terminators of WebVTT, characters like U+2028 do not break lines
_LINE_TERMINATOR = re.compile(r'\r\n|\r|\n')

# a path to a file or a folder, as a string or a path object such as
# pathlib.Path
_Path = str | os.PathLike[str]

# the line number where a block of text starts, from 1, and its lines
_NumberedBlock = tuple[int, list[str]]

# the characters WebVTT treats as spaces, as in cue tags and settings
_ASCII_WHITESPACE = ' \t\n\f\r'
_ASCII_WHITESPACE_RUN = re.compile(f'[{_ASCII_WHITESPACE}]+')


def _split_lines(text: str) -> list[str]:
    """
    Split text into lines on CR, LF and CRLF.

    Works like ``str.splitlines`` but only breaks lines on the WebVTT line
    terminators, other characters like U+2028 or form feed stay in the line.

    :param text: the text to split
    :returns: the list of lines, without a final empty line when the text
        ends with a line terminator
    """
    lines = _LINE_TERMINATOR.split(text)
    if not lines[-1]:
        lines.pop()
    return lines


def _iter_cue_text_tokens(
        text: str
        ) -> Generator[tuple[str, bool], None, None]:
    """
    Iterate the text and the cue tags of cue text.

    A tag runs from a ``<`` to the next ``>``, so a tag can span line
    breaks. A ``<`` without a ``>`` after it is part of the text. The scan
    stops at the first one, as no later ``<`` in the text has a ``>``
    after it either, which keeps it linear in the length of the text.

    :param text: the cue text
    :returns: tuples of the text or the tag content and true for a tag
    """
    pos = 0
    while True:
        start = text.find('<', pos)
        if start < 0:
            break
        end = text.find('>', start + 1)
        if end < 0:
            break
        if start > pos:
            yield text[pos:start], False
        yield text[start + 1:end], True
        pos = end + 1
    if pos < len(text):
        yield text[pos:], False


def _normalize_annotation(text: str) -> str:
    """
    Return a cue tag annotation without extra ASCII whitespace.

    As in the WebVTT start tag annotation state, leading and trailing
    ASCII whitespace is removed and each run of ASCII whitespace is
    replaced with a single space.

    :param text: the annotation, as written
    :returns: the annotation, normalized
    """
    return _ASCII_WHITESPACE_RUN.sub(' ', text.strip(_ASCII_WHITESPACE))


def _check_errors(errors: str | None) -> None:
    """
    Check the name of an error handler for decoding.

    Decoding only looks up the handler on invalid bytes, so a wrong name
    would otherwise go unnoticed until a file has them.

    :param errors: the name of the error handler, or None for the default
    :raises LookupError: if the error handler is not known
    """
    if errors is not None:
        codecs.lookup_error(errors)


class FileWrapper:
    """File handling functionality with built-in support for Byte OrderMark."""

    def __init__(
            self,
            file_path: _Path,
            mode: str | None = None,
            encoding: str | None = None,
            errors: str | None = None
            ):
        """
        Initialize.

        :param file_path: path to the file
        :param mode: mode in which the file is opened
        :param encoding: name of the encoding used to decode the file
        :param errors: how decoding errors are handled, as in open()

        :raises LookupError: if the error handler is not known
        """
        _check_errors(errors)
        self.file_path = file_path
        self.mode = mode or 'r'
        self.errors = errors
        self.bom_encoding = self.detect_bom_encoding(file_path)
        self.encoding = (self.bom_encoding or
                         encoding or
                         'utf-8'
                         )

    @classmethod
    def open(
            cls,
            file_path: _Path,
            mode: str | None = None,
            encoding: str | None = None,
            errors: str | None = None
            ) -> 'FileWrapper':
        """
        Open a file.

        :param file_path: path to the file
        :param mode: mode in which the file is opened
        :param encoding: name of the encoding used to decode the file
        :param errors: how decoding errors are handled, as in open()
        """
        return cls(file_path, mode, encoding, errors)

    def __enter__(self) -> 'FileWrapper':
        """Enter context."""
        self.file = open(
            file=self.file_path,
            mode=self.mode,
            encoding=self.encoding,
            errors=self.errors
            )
        if self.bom_encoding:
            self.file.seek(len(CODEC_BOMS[self.bom_encoding]))
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Exit context."""
        self.file.close()

    @staticmethod
    def detect_bom_encoding(file_path: _Path) -> str | None:
        """
        Detect the encoding of a file based on the presence of the BOM.

        :param file_path: path to the file
        :returns: the encoding if BOM is found or None.
        """
        with open(file_path, mode='rb') as f:
            return _bom_encoding(f.read(4))


def _bom_encoding(data: bytes) -> str | None:
    """
    Return the encoding of the BOM at the start of the data.

    :param data: the data, or its first bytes
    :returns: the encoding if BOM is found or None.
    """
    for encoding, bom in CODEC_BOMS.items():
        if data.startswith(bom):
            return encoding
    return None


def _write_text_file(
        file_path: _Path,
        text: str,
        encoding: str | None = None
        ) -> None:
    """
    Write text to a file, encoding all of it before the file is opened.

    An error while encoding, such as a character the encoding cannot write
    or an unknown encoding, is raised before the file is opened, so an
    existing file is left unchanged. Line feeds are written as the line
    separator of the platform, as in a file opened in text mode.

    :param file_path: path to the file
    :param text: the text to write
    :param encoding: name of the encoding used to encode the text, the
        preferred encoding of the locale if None, as in a file opened in
        text mode
    """
    if encoding is None:
        encoding = locale.getpreferredencoding(False)

    data = text.replace('\n', os.linesep).encode(encoding)
    with open(file_path, 'wb') as f:
        f.write(data)


def iter_blocks_of_lines(
        lines: Iterable[str]
        ) -> Generator[list[str], None, None]:
    """
    Iterate blocks of text.

    :param lines: lines of text.
    """
    for _, block in _iter_numbered_blocks(lines):
        yield block


def _iter_numbered_blocks(
        lines: Iterable[str]
        ) -> Generator[_NumberedBlock, None, None]:
    """
    Iterate blocks of text with the line number where each one starts.

    :param lines: lines of text.
    :returns: the line number where each block starts, from 1, and its lines
    """
    current_text_block: list[str] = []
    number = 0

    for number, line in enumerate(lines, 1):
        if line.strip():
            current_text_block.append(line)
        elif current_text_block:
            yield number - len(current_text_block), current_text_block
            current_text_block = []

    if current_text_block:
        yield number + 1 - len(current_text_block), current_text_block


def _strip_css_comments(css: str) -> str:
    """
    Remove the comments from CSS.

    A comment without an end runs to the end of the CSS. This scans for
    the comments with ``str.find``, as a non-greedy regex for them rescans
    the rest of the CSS from every comment start without an end, which is
    quadratic in the length of the CSS.

    :param css: the CSS text
    :returns: the CSS without comments
    """
    parts = []
    pos = 0
    while True:
        start = css.find('/*', pos)
        if start < 0:
            break
        parts.append(css[pos:start])
        end = css.find('*/', start + 2)
        if end < 0:
            return ''.join(parts)
        pos = end + 2
    parts.append(css[pos:])
    return ''.join(parts)


# a character of a CSS identifier, or an escape, as in ``c\+1`` or
# ``\31 a``. Each character matches one alternative only, and a hex escape
# takes all its digits, so that a selector that does not match fails in
# linear time
_CSS_IDENTIFIER = (
    r'(?:[a-zA-Z0-9_-]|[^\x00-\x7f]|'
    r'\\(?:(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{1,5}(?![0-9a-fA-F]))[ \t\n\r\f]?|'
    r'[^\n\r\f0-9a-fA-F]))+'
    )

# a class selector of CSS, as in ``.loud``
_CSS_CLASS = re.compile(rf'\.({_CSS_IDENTIFIER})')

# an escape of CSS, as in ``\+`` or ``\31 a``, with the code point of its
# hex digits, or else the escaped character
_CSS_ESCAPE = re.compile(
    r'\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(.))', re.DOTALL
    )


def _css_unescape(identifier: str) -> str:
    r"""
    Return a CSS identifier without its escapes.

    :param identifier: the identifier, as in ``c\+1`` or ``\31 a``
    :returns: the identifier, as in ``c+1`` or ``1a``
    """
    def unescape(match: re.Match[str]) -> str:
        if match.group(2) is not None:
            return match.group(2)
        code = int(match.group(1), 16)
        if code == 0 or 0xd800 <= code <= 0xdfff or code > 0x10ffff:
            return '\ufffd'
        return chr(code)

    return _CSS_ESCAPE.sub(unescape, identifier)
