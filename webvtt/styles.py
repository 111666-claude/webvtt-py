"""Evaluation of the styles of the cues.

A ``STYLE`` block is a CSS style sheet, and a viewer needs the final style
of each piece of a cue to draw it, e.g. to know whether a word is white or
yellow. Saving the ``STYLE`` text does not tell: the rules may set a colour
for a voice, a language or a class, and the cue tags and the default styles
of WebVTT also apply. This module evaluates the ``::cue`` rules of the
styles for the fragments of a cue, see `cue_styles` and `CueStyle`.

The evaluator follows the WebVTT specification and CSS:

* The style sheets are read in their order. A rule that is not a ``::cue``
  rule, whose selector is not one of the supported selectors, or that nests
  other rules is ignored, and so are the declarations of unknown properties
  and the declarations whose values are not valid. Reading a style never
  fails for CSS that is not valid: the rule is ignored instead. CSS comments
  are removed. A style sheet that nests rules deeper than `MAX_NESTING`,
  such as a malformed ``@media`` rule, raises ``ValueError``, so a file
  cannot make the evaluation run out of memory.

* The supported selectors are ``::cue``, ``::cue()``, and the type, class
  and ``[voice=...]`` or ``[lang=...]`` attribute selectors of
  ``::cue(...)``, as in ``::cue(b)``, ``::cue(.loud)``,
  ``::cue(c.loud.red)``, ``::cue(v[voice="Esme"])``,
  ``::cue(lang[lang="en-US"])`` or ``::cue(*)``. A selector list applies a
  rule to each of its selectors that is supported, and a rule with a
  selector that is not supported is ignored as a whole, as CSS ignores a
  rule with an invalid selector. A selector with a combiner, such as
  ``::cue(c > b)``, with another pseudo-class, such as ``::cue(b:not(i))``
  or ``::cue(#id)``, or an attribute other than ``voice`` and ``lang`` is
  not supported.

* A rule applies to a fragment when its selector matches one of the tags
  that contains it. ``::cue`` matches the cue and every tag of it, as in
  WebVTT, where it applies to each internal node object, and the
  properties of a tag are inherited by the text it contains. The rule with
  the highest specificity applies, and then the one that comes last, as
  in CSS. The specificity of a selector counts its classes and attribute
  selectors, then its type selector.

* The default styles of WebVTT apply where no rule does: the text is
  white, ``b`` is bold, ``i`` is italic and ``u`` is underlined, and the
  default colour classes, such as ``yellow`` or ``bg_black``, set the
  colour and the background of the tag that has them, the last class of
  each kind applying. Other tags, such as ``v`` and ``lang``, do not set
  a style by themselves.

* A tag whose text is not styled, a tag with no text, a timestamp tag and
  an unknown tag are ignored, keeping their text. An end tag closes the
  innermost open tag only when it names it, as in the WebVTT cue text
  parsing rules, so a stray end tag never closes an outer tag and no
  cycle can be formed, and the tags left open at the end stay open.

* The ``color``, ``font-style``, ``font-weight``, ``font-variant`` and
  ``text-decoration`` are inherited by the text of a tag, while the
  ``background-color`` is not, as in CSS. So the background of a fragment
  is the one of the innermost tag that sets it, or transparent.

The text of the fragments is as written, with its character references and
line breaks. The values of the styles are kept as written, so colours are
not converted: ``red``, ``#ff0000`` and ``rgb(255, 0, 0)`` are different
values, although they are the same colour.
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from .models import Caption, Style
from .utils import (
    _ASCII_WHITESPACE,
    _CSS_CLASS,
    _CSS_IDENTIFIER,
    _css_unescape,
    _iter_cue_text_tokens,
    _normalize_annotation,
    _strip_css_comments
    )

# the biggest nesting of rules that is read, so a style that nests rules
# without end, such as a malformed @media rule, is rejected instead of
# being read until the memory runs out
MAX_NESTING = 32

_CUE = '::cue'
_CUE_ARGUMENTS = '::cue('

# the CSS properties that are read. The other properties, such as
# ``font-family`` or ``background-image``, are not part of the style
_PROPERTIES = frozenset((
    'color',
    'background-color',
    'background',
    'font-style',
    'font-weight',
    'font-variant',
    'text-decoration',
    ))

# the style of a fragment before the tags and the rules, as WebVTT gives
# the cue text a white colour and no other style. The background of the cue
# is on the cue background box, not on the text, so it is transparent
_INITIAL_STYLE = {
    'color': 'rgba(255, 255, 255, 1)',
    'background-color': 'transparent',
    'font-style': 'normal',
    'font-weight': 'normal',
    'font-variant': 'normal',
    'text-decoration': 'none',
    }

# the style that each tag gives to the text it contains, as WebVTT does
_TAG_PROPERTIES: dict[str, tuple[str, str]] = {
    'b': ('font-weight', 'bold'),
    'i': ('font-style', 'italic'),
    'u': ('text-decoration', 'underline'),
    }

# the tags that have a style of their own when a rule matches them, and
# the tags that only group the text
_TAGS = frozenset(('b', 'i', 'u', 'c', 'v', 'lang', 'ruby', 'rt'))

# the default classes of WebVTT, which are presentational hints for the
# text of the tags that have them, with the colours of the specification
_DEFAULT_COLOURS = {
    'white': 'rgba(255, 255, 255, 1)',
    'lime': 'rgba(0, 255, 0, 1)',
    'cyan': 'rgba(0, 255, 255, 1)',
    'red': 'rgba(255, 0, 0, 1)',
    'yellow': 'rgba(255, 255, 0, 1)',
    'magenta': 'rgba(255, 0, 255, 1)',
    'blue': 'rgba(0, 0, 255, 1)',
    'black': 'rgba(0, 0, 0, 1)',
    }
_DEFAULT_BACKGROUNDS = {
    f'bg_{name}': colour for name, colour in _DEFAULT_COLOURS.items()
    }

# a tag name followed by its classes, as in ``c.loud`` or ``v.first.Esme``,
# and its annotation after the ASCII whitespace. The name starts at the
# start of the tag, as in the WebVTT tokenizer
_START_TAG = re.compile(
    rf'([^{_ASCII_WHITESPACE}]*)[{_ASCII_WHITESPACE}]*(.*)', re.DOTALL
    )

# the type selector and the classes of a compound selector, as in ``c.loud``
_NAME_AND_CLASSES = re.compile(
    rf'\s*(\*|[a-zA-Z][a-zA-Z0-9_-]*)?((?:\.{_CSS_IDENTIFIER})*)\s*'
    )

# an attribute selector, as in ``[voice="Esme"]`` or ``[lang=en i]``
_ATTRIBUTE_SELECTOR = re.compile(
    r'''\s*\[\s*([a-zA-Z-]+)\s*'''
    r'''(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*))\s*'''
    r'''([iIsS])?\s*)?\]\s*''',
    re.DOTALL
    )

# a pseudo-class or a pseudo-element after ::cue(), such as :past or
# ::before, which is read as it does not change which tags a rule matches
_SUFFIX = re.compile(r'(?:::?[\w-]+)*')

# a hex colour, of 3, 4, 6 or 8 digits
_HEX_COLOUR = re.compile(
    r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})'
    )

# a colour function, such as ``rgb(255, 0, 0)`` or ``hsl(0 100% 50%)``
_COLOUR_FUNCTION = re.compile(
    r'(?P<name>rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)'
    r'\(\s*(?P<values>[^()]*?)\s*\)',
    re.IGNORECASE
    )

# a component of a colour function, such as ``255``, ``50%`` or ``.5``
_COLOUR_COMPONENT = re.compile(
    r'[-+]?\.?[0-9]+(?:\.[0-9]+)?(?:%|deg|grad|rad|turn)?|[a-zA-Z]+'
    )

# the named colours of CSS Color 4, which a declaration can use as a
# colour, besides the keywords of CSS
_NAMED_COLOURS = frozenset((
    'aliceblue', 'antiquewhite', 'aqua', 'aquamarine', 'azure', 'beige',
    'bisque', 'black', 'blanchedalmond', 'blue', 'blueviolet', 'brown',
    'burlywood', 'cadetblue', 'chartreuse', 'chocolate', 'coral',
    'cornflowerblue', 'cornsilk', 'crimson', 'cyan', 'darkblue', 'darkcyan',
    'darkgoldenrod', 'darkgray', 'darkgreen', 'darkgrey', 'darkkhaki',
    'darkmagenta', 'darkolivegreen', 'darkorange', 'darkorchid', 'darkred',
    'darksalmon', 'darkseagreen', 'darkslateblue', 'darkslategray',
    'darkslategrey', 'darkturquoise', 'darkviolet', 'deeppink',
    'deepskyblue', 'dimgray', 'dimgrey', 'dodgerblue', 'firebrick',
    'floralwhite', 'forestgreen', 'fuchsia', 'gainsboro', 'ghostwhite',
    'gold', 'goldenrod', 'gray', 'green', 'greenyellow', 'grey', 'honeydew',
    'hotpink', 'indianred', 'indigo', 'ivory', 'khaki', 'lavender',
    'lavenderblush', 'lawngreen', 'lemonchiffon', 'lightblue', 'lightcoral',
    'lightcyan', 'lightgoldenrodyellow', 'lightgray', 'lightgreen',
    'lightgrey', 'lightpink', 'lightsalmon', 'lightseagreen', 'lightskyblue',
    'lightslategray', 'lightslategrey', 'lightsteelblue', 'lightyellow',
    'lime', 'limegreen', 'linen', 'magenta', 'maroon', 'mediumaquamarine',
    'mediumblue', 'mediumorchid', 'mediumpurple', 'mediumseagreen',
    'mediumslateblue', 'mediumspringgreen', 'mediumturquoise',
    'mediumvioletred', 'midnightblue', 'mintcream', 'mistyrose', 'moccasin',
    'navajowhite', 'navy', 'oldlace', 'olive', 'olivedrab', 'orange',
    'orangered', 'orchid', 'palegoldenrod', 'palegreen', 'paleturquoise',
    'palevioletred', 'papayawhip', 'peachpuff', 'peru', 'pink', 'plum',
    'powderblue', 'purple', 'rebeccapurple', 'red', 'rosybrown',
    'royalblue', 'saddlebrown', 'salmon', 'sandybrown', 'seagreen',
    'seashell', 'sienna', 'silver', 'skyblue', 'slateblue', 'slategray',
    'slategrey', 'snow', 'springgreen', 'steelblue', 'tan', 'teal',
    'thistle', 'tomato', 'turquoise', 'violet', 'wheat', 'white',
    'whitesmoke', 'yellow', 'yellowgreen',
    'currentcolor', 'transparent',
    ))

_FONT_STYLES = frozenset(('normal', 'italic', 'oblique'))
_FONT_VARIANTS = frozenset(('normal', 'small-caps'))
_FONT_WEIGHTS = frozenset(('normal', 'bold', 'bolder', 'lighter'))
_DECORATIONS = frozenset((
    'none', 'underline', 'overline', 'line-through', 'blink', 'line',
    'solid', 'double', 'dotted', 'dashed', 'wavy',
    ))

# a background shorthand, split on the parts that are not in parentheses
_BACKGROUND_PARTS = re.compile(r'(?:[^\s(]+|\([^)]*\))+')


@dataclass(frozen=True)
class CueStyle:
    """
    The style of a fragment of cue text, with the values as evaluated.

    A value is as written in the style, such as ``#ff0`` for
    ``color: #ff0``, or as WebVTT gives it, such as ``bold`` for the
    ``font-weight`` of the text in a ``b`` tag. Colours are not converted,
    so ``red``, ``#ff0000`` and ``rgb(255, 0, 0)`` are different values
    although they are the same colour.

    Unlike the declarations of the style sheets, which are only inherited
    as in CSS, every property has a value: ``color``, ``font-style``,
    ``font-weight``, ``font-variant`` and ``text-decoration`` are inherited
    from the tags that contain the fragment, and ``background_color`` is
    not, as in CSS, so it is transparent in a fragment without one.

    :param text: the text of the fragment, the text between cue tags, as
        written, with its character references and line breaks
    :param color: the text colour
    :param background_color: the background colour of the text
    :param font_style: the font style, ``normal``, ``italic`` or
        ``oblique``
    :param font_weight: the font weight, ``normal``, ``bold``,
        ``bolder``, ``lighter`` or a number
    :param font_variant: the font variant, ``normal`` or ``small-caps``
    :param text_decoration: the text decoration, ``none``, ``underline``,
        or another decoration of CSS
    """

    text: str
    color: str
    background_color: str
    font_style: str
    font_weight: str
    font_variant: str
    text_decoration: str


@dataclass(frozen=True)
class _Element:
    """A tag of cue text, or the root of the fragments of a cue."""

    #: the tag name, lowercase, empty for the root of the cue
    name: str
    #: the classes of the tag, in their order
    classes: tuple[str, ...] = ()
    #: the name, lowercase, and value of its ``voice`` or ``lang``
    #: attribute, if any
    attribute: tuple[str, str] | None = None

    def style(self) -> dict[str, str]:
        """
        Return the default style of the tag.

        The style of a ``v`` or ``lang`` tag does not set a property, as
        its text is styled like the text of the cue, and the presentation
        hints of the default classes apply to any cue component with them,
        as in WebVTT, the last class of each kind applying.

        :returns: the property and value of each style of the tag
        """
        property = _TAG_PROPERTIES.get(self.name)
        style = {} if property is None else {property[0]: property[1]}
        for name in self.classes:
            if name in _DEFAULT_COLOURS:
                style['color'] = _DEFAULT_COLOURS[name]
            if name in _DEFAULT_BACKGROUNDS:
                style['background-color'] = _DEFAULT_BACKGROUNDS[name]
        return style


# the root of the fragments, which holds the style of the whole cue
_ROOT = _Element('')


@dataclass(frozen=True)
class _Simple:
    """A compound selector of ``::cue()``, as in ``c.loud``."""

    #: the type selector, lowercase, or None to match any tag
    name: str | None
    #: the classes that the tag must have
    classes: frozenset[str]
    #: the name, lowercase, and value of the attribute of the tag, and
    #: whether the value is compared without case
    attribute: tuple[str, str, bool] | None

    @property
    def specificity(self) -> tuple[int, int, int]:
        """Return the specificity of the selector, as in CSS."""
        return (
            len(self.classes) + int(self.attribute is not None),
            int(self.name is not None),
            0
            )

    def matches(self, element: _Element) -> bool:
        """
        Return whether a tag matches the selector.

        As in CSS, the value of the attribute of the selector is compared
        to the value of the tag, or to its lowercase value when the
        selector has the ``i`` flag.

        :param element: the tag
        :returns: true if the type, the classes and the attribute of the
            tag match the selector
        """
        if self.name is not None and self.name != element.name:
            return False
        if not self.classes <= frozenset(element.classes):
            return False
        if self.attribute is None:
            return True
        if element.attribute is None:
            return False
        name, value, without_case = self.attribute
        if name != element.attribute[0]:
            return False
        if without_case:
            return value.casefold() == element.attribute[1].casefold()
        return value == element.attribute[1]


@dataclass(frozen=True)
class _Rule:
    """A declaration of a supported ``::cue`` rule."""

    #: the compound selector, or None for ``::cue``, which matches the cue
    selector: _Simple | None
    #: the specificity of the selector, the position of its rule in the
    #: style sheets, and the position of its declaration in the rule
    order: tuple[tuple[int, int, int], tuple[int, int], int]
    #: the property, lowercase
    property: str
    #: the value, as written, without ``!important``
    value: str


def _split_on_unquoted(text: str, character: str) -> list[str]:
    """
    Split text on a character that is not escaped or inside quotes.

    :param text: the text to split
    :param character: the character to split on
    :returns: the parts, without the character
    """
    parts = []
    start = 0
    quote = ''
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == character:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def _block_end(css: str, start: int) -> int:
    """
    Return the position of the brace that closes a block.

    :param css: the CSS text
    :param start: the position of the opening brace
    :returns: the position of the closing brace, or -1 when the block is
        not closed

    :raises ValueError: if the block nests other blocks deeper than
        `MAX_NESTING`
    """
    depth = 0
    quote = ''
    index = start
    while index < len(css):
        char = css[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == '{':
            depth += 1
            if depth > MAX_NESTING:
                raise ValueError(f'CSS rules nested deeper than {MAX_NESTING}')
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _at_rule_end(css: str, start: int) -> int:
    """
    Return the position after an at-rule, which is skipped.

    :param css: the CSS text
    :param start: the position of the ``@``
    :returns: the position after the ``;`` or the block of the at-rule, or
        -1 when it is not closed

    :raises ValueError: if the block nests other blocks deeper than
        `MAX_NESTING`
    """
    index = start
    quote = ''
    while index < len(css):
        char = css[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == ';':
            return index + 1
        elif char == '{':
            end = _block_end(css, index)
            return -1 if end < 0 else end + 1
        elif char == '}':
            return index + 1
        index += 1
    return -1


def _rule_blocks(css: str) -> Iterator[tuple[str, str]]:
    """
    Iterate the selectors and the declarations of the rules of CSS.

    At-rules, such as ``@media``, are left out with their blocks, and rules
    that nest other rules are left out, as WebVTT has no nested rules. A
    closing brace without a rule is skipped, and the rules after a block
    that is not closed are not read.

    :param css: the CSS text, without comments
    :returns: the selectors as written, and the declarations of each rule

    :raises ValueError: if the rules are nested deeper than `MAX_NESTING`
    """
    index = 0
    while index < len(css):
        index = _next_rule(css, index)
        if index < 0:
            return
        if css[index] == '@':
            index = _at_rule_end(css, index)
            if index < 0:
                return
            continue

        brace = css.find('{', index)
        close = css.find('}', index)
        if brace < 0 or 0 <= close < brace:
            index = len(css) if close < 0 else close + 1
            continue

        end = _block_end(css, brace)
        if end < 0:
            return
        declarations = css[brace + 1:end]
        if not _has_block(declarations):
            yield css[index:brace], declarations
        index = end + 1


def _has_block(text: str) -> bool:
    """
    Return whether text starts a block outside quotes.

    A brace that is escaped or in quotes, as in ``content: "}"``, does not
    start a block.

    :param text: the declarations of a rule
    :returns: true if a brace starts a block
    """
    quote = ''
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == '{':
            return True
        index += 1
    return False


def _next_rule(css: str, index: int) -> int:
    """
    Return the position where the next rule starts.

    :param css: the CSS text
    :param index: the position to search from
    :returns: the position of the first character that is not whitespace
        or a closing brace without a rule, or -1 at the end
    """
    while index < len(css) and (
            css[index] in _ASCII_WHITESPACE or css[index] == '}'):
        index += 1
    return index if index < len(css) else -1


def _matching_parenthesis(text: str, start: int) -> int:
    """
    Return the position of the parenthesis that closes one.

    :param text: the text
    :param start: the position of the opening parenthesis
    :returns: the position of the closing parenthesis, or -1 when it is
        not closed
    """
    depth = 0
    quote = ''
    index = start
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _selector_argument(selector: str) -> tuple[bool, str]:
    """
    Return whether a selector is a ``::cue`` selector, and its argument.

    A selector is supported when it is ``::cue``, or ``::cue()`` with its
    argument, and then only by pseudo-classes or pseudo-elements without
    an argument, such as ``:past``, which do not change which tags the
    rule applies to.

    :param selector: one selector of a rule, as written
    :returns: true and the argument of ``::cue()``, empty for ``::cue``
        and None for a selector that is not supported
    """
    selector = selector.strip()
    if selector == _CUE:
        return True, ''
    if not selector.startswith(_CUE_ARGUMENTS):
        return False, ''
    end = _matching_parenthesis(selector, len(_CUE_ARGUMENTS) - 1)
    if end < 0:
        return False, ''
    if not _suffix_is_supported(selector[end + 1:]):
        return False, ''
    argument = selector[len(_CUE_ARGUMENTS):end]
    if not argument.strip() or _has_unsupported_syntax(argument):
        # ::cue() with no selector is not valid CSS, and another selector
        # inside the parenthesis selects other elements
        return False, ''
    return True, argument


def _has_unsupported_syntax(selector: str) -> bool:
    """
    Return whether a compound selector has syntax that is not supported.

    A pseudo-class, a pseudo-element or a combiner selects other elements,
    so a rule with one is not read. They are looked for outside quotes, as
    a voice or a language name can have a colon or a combiner, as in
    ``::cue(v[voice="a:b"])``.

    :param selector: the argument of ``::cue()``, as written
    :returns: true if it has a pseudo-class, a pseudo-element or a
        combiner
    """
    quote = ''
    index = 0
    while index < len(selector):
        char = selector[index]
        if char == '\\':
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char in ':>+~':
            return True
        index += 1
    return False


def _suffix_is_supported(suffix: str) -> bool:
    """
    Return whether the text after ``::cue()`` keeps its properties.

    Only a pseudo-class or a pseudo-element without an argument is read,
    such as ``:past``, as another one, such as ``:not(i)``, selects other
    elements.

    :param suffix: the text after the closing parenthesis
    :returns: true if the text is empty, or only such pseudo-classes and
        pseudo-elements
    """
    return bool(_SUFFIX.fullmatch(suffix.strip()))


def _simple_selector(argument: str) -> _Simple | None:
    """
    Return the compound selector of the argument of ``::cue()``.

    :param argument: the argument of ``::cue()``, as written
    :returns: the selector, or None for a selector that is not supported
    """
    name: str | None = None
    classes: set[str] = set()

    match = _NAME_AND_CLASSES.match(argument)
    # always matches, as every part can be empty
    assert match is not None
    # the universal selector matches every tag, as no name does
    if match.group(1) and match.group(1) != '*':
        name = match.group(1).lower()
    classes.update(
        _css_unescape(class_name)
        for class_name in _CSS_CLASS.findall(match.group(2))
        )
    rest = argument[match.end():]
    attribute, rest = _attribute(rest)
    if isinstance(attribute, _Unsupported):
        return None
    if rest.strip():
        return None
    return _Simple(name, frozenset(classes), attribute)


class _Unsupported:
    """An attribute selector that is not supported."""


# an attribute selector that is not for a voice or a language, or that
# compares the value in another way
_UNSUPPORTED_ATTRIBUTE = _Unsupported()


def _attribute(
        text: str
        ) -> tuple[tuple[str, str, bool] | _Unsupported | None, str]:
    """
    Read the attribute selector of a compound selector, if any.

    Only an attribute selector for the ``voice`` or ``lang`` attribute
    with the ``=`` match flag is supported, which is what WebVTT uses to
    select a voice or a language span.

    :param text: the text after the type selector and the classes
    :returns: the name and value of the attribute, lowercase, with whether
        it is compared without case, None when there is none, or
        `_UNSUPPORTED_ATTRIBUTE`, and the text that follows
    """
    match = _ATTRIBUTE_SELECTOR.match(text)
    if match is None:
        return None, text
    rest = text[match.end():]
    if not match.group(2):
        # an attribute selector without a value is not supported
        return _UNSUPPORTED_ATTRIBUTE, rest
    name = match.group(1).lower()
    if match.group(2) != '=' or name not in ('voice', 'lang'):
        # the other match flags, such as ~=, are not supported
        return _UNSUPPORTED_ATTRIBUTE, rest
    value = next(item for item in match.group(3, 4, 5) if item is not None)
    return (
        name,
        _css_unescape(value),
        bool(match.group(6) and match.group(6).lower() == 'i')
        ), rest


def _is_colour(value: str) -> bool:
    """
    Return whether a value is a colour of CSS.

    :param value: the value of a declaration
    :returns: true if it is a named colour, a hex colour or a colour
        function with the right number of valid components
    """
    if value.lower() in _NAMED_COLOURS or _HEX_COLOUR.fullmatch(value):
        return True
    match = _COLOUR_FUNCTION.fullmatch(value)
    if match is None:
        return False
    # a colour function has components separated by commas or spaces, and
    # an opacity after a solidus
    parts = re.split(r'[,/\s]+', match.group('values').strip())
    if any(not _COLOUR_COMPONENT.fullmatch(part) for part in parts):
        return False
    return len(parts) in (
        (3, 4) if match.group('name').lower() in
        ('rgb', 'rgba', 'hsl', 'hsla', 'hwb')
        else (1, 3, 4)
        )


def _is_font_weight(value: str) -> bool:
    """Return whether a value is a font weight of CSS."""
    if value.lower() in _FONT_WEIGHTS:
        return True
    return value.isdigit() and 1 <= int(value) <= 1000


def _is_decoration(value: str) -> bool:
    """Return whether a value starts with a text decoration of CSS."""
    words = value.lower().split()
    return bool(words) and words[0] in _DECORATIONS


# the check of the value of each property that is read
_VALIDATORS = {
    'color': _is_colour,
    'background-color': _is_colour,
    'font-style': lambda value: value.lower() in _FONT_STYLES,
    'font-weight': _is_font_weight,
    'font-variant': lambda value: value.lower() in _FONT_VARIANTS,
    'text-decoration': _is_decoration,
    }


def _background_colour(value: str) -> str | None:
    """
    Return the colour of a background shorthand.

    :param value: the value of a ``background`` declaration
    :returns: the colour, its last part that is a colour, or None
    """
    parts: list[str] = _BACKGROUND_PARTS.findall(value)
    for part in reversed(parts):
        if _is_colour(part):
            return part
    return None


def _declarations(
        declarations: str
        ) -> Iterator[tuple[str, str]]:
    """
    Iterate the declarations of a rule that are read.

    A declaration without a colon or a name, of a property that is not
    read, or with a value that is not valid is skipped. A ``background``
    shorthand is read as its background colour. The last declaration of a
    property is returned once, as in CSS.

    :param declarations: the declarations as written
    :returns: the property, lowercase, and the value without
        ``!important``
    """
    values: dict[str, str] = {}
    for declaration in _split_on_unquoted(declarations, ';'):
        name, separator, value = declaration.partition(':')
        name = name.strip().lower()
        value = value.partition('!')[0].strip()
        if not separator or not name or not value:
            continue
        if name == 'background':
            colour = _background_colour(value)
            if colour is not None:
                values['background-color'] = colour
        elif name in _PROPERTIES and _VALIDATORS[name](value):
            values[name] = value
    return iter(values.items())


def _rules(styles: Iterable[Style]) -> list[_Rule]:
    """
    Return the rules of the styles that can apply to a cue.

    :param styles: the styles, in their order
    :returns: the rules, in their order

    :raises TypeError: if styles is not an iterable of `Style`
    :raises ValueError: if a style sheet nests rules deeper than
        `MAX_NESTING`
    """
    rules: list[_Rule] = []
    for style_number, style in enumerate(styles, 1):
        if not isinstance(style, Style):
            raise TypeError(f'Expected a Style, got {style!r}')
        position = 0
        for selectors, declarations in _rule_blocks(
                _strip_css_comments(style.text)):
            position += 1
            values = list(_declarations(declarations))
            if values:
                rules.extend(_rules_of_rule(
                    selectors, values, (style_number, position)
                    ))
    return rules


def _rules_of_rule(
        selectors: str,
        values: Sequence[tuple[str, str]],
        position: tuple[int, int]
        ) -> Iterator[_Rule]:
    """
    Return the rules of each selector of a rule of CSS that is read.

    As in CSS, a rule with a selector that is not supported is left out,
    also for its other selectors, as the rule is not valid.

    :param selectors: the selectors of the rule, as written
    :param values: the property and value of each declaration
    :param position: the position of the rule in the style sheets
    :returns: a rule for each selector that is read and each declaration
    """
    simple_selectors = [
        _read_selector(selector)
        for selector in _split_on_unquoted(selectors, ',')
        ]
    if any(selector is _UNSUPPORTED_SELECTOR
           for selector in simple_selectors):
        return
    for simple in simple_selectors:
        assert simple is None or isinstance(simple, _Simple)
        specificity = (0, 0, 0) if simple is None else simple.specificity
        for index, (name, value) in enumerate(values):
            yield _Rule(simple, (specificity, position, index), name, value)


# the selector of a rule that is not supported
_UNSUPPORTED_SELECTOR = _Unsupported()


def _read_selector(
        selector: str
        ) -> _Simple | None | _Unsupported:
    """
    Return the selector of one part of the selectors of a rule.

    :param selector: the selector, as written
    :returns: the compound selector, None for ``::cue``, or
        `_UNSUPPORTED_SELECTOR` for a selector that is not supported
    """
    read, argument = _selector_argument(selector)
    if not read:
        return _UNSUPPORTED_SELECTOR
    if not argument:
        return None
    simple = _simple_selector(argument)
    return _UNSUPPORTED_SELECTOR if simple is None else simple


def _declared(
        rules: Sequence[_Rule],
        element: _Element
        ) -> dict[str, str]:
    """
    Return the declarations of the rules that match a tag.

    ``::cue`` matches every tag of the cue, as in WebVTT, where it applies
    to each WebVTT internal node object.

    :param rules: the rules of the styles
    :param element: the tag
    :returns: the value of each property, from the rule with the highest
        specificity, and then the one that comes last
    """
    values: dict[str, str] = {}
    orders: dict[str, tuple[tuple[int, int, int], tuple[int, int], int]] = {}
    for rule in rules:
        if rule.selector is not None and not rule.selector.matches(element):
            continue
        order = orders.get(rule.property)
        if order is None or rule.order >= order:
            orders[rule.property] = rule.order
            values[rule.property] = rule.value
    return values


def _open(
        values: dict[str, str],
        element: _Element,
        rules: Sequence[_Rule]
        ) -> dict[str, str]:
    """
    Return the style of the text in a tag.

    The style of a tag is the style of the tag that contains it, with its
    own default style and the declarations of the rules that match it. The
    background colour is not inherited, as in CSS, so it is the one of the
    tag that sets it, or transparent.

    :param values: the style of the text containing the tag
    :param element: the tag
    :param rules: the rules of the styles
    :returns: the style of the text in the tag, a new mapping
    """
    style = dict(values)
    # the background of the text is not inherited, unlike its colour
    style['background-color'] = _INITIAL_STYLE['background-color']
    style.update(element.style())
    style.update(_declared(rules, element))
    return style


def _tag(content: str) -> tuple[str, _Element] | None:
    """
    Read the cue tag of a token of cue text.

    :param content: the text between ``<`` and ``>``
    :returns: ``'start'`` or ``'end'`` and the element of a supported tag,
        or None for another tag, such as a timestamp tag
    """
    if content.startswith('/'):
        name = content[1:].strip(_ASCII_WHITESPACE).lower()
        return ('end', _Element(name)) if name in _TAGS else None

    match = _START_TAG.match(content)
    # always matches, as every part can be empty
    assert match is not None
    name, *classes = match.group(1).split('.')
    name = name.lower()
    if name not in _TAGS:
        return None

    attribute = None
    if name in ('v', 'lang') and match.group(2):
        # the annotation has no leading whitespace, and the name of a
        # voice or a language is normalized as in WebVTT
        attribute = (
            'voice' if name == 'v' else 'lang',
            _normalize_annotation(match.group(2))
            )
    return ('start', _Element(name, tuple(classes), attribute))


def _fragments(
        raw_text: str,
        rules: Sequence[_Rule]
        ) -> Iterator[tuple[str, dict[str, str]]]:
    """
    Iterate the fragments of cue text with the style of their text.

    The tags are read as in the WebVTT cue text parsing rules: a tag can
    span line breaks, a tag that is not supported is ignored, and an end
    tag closes the tag that it names only when it is the innermost one, so
    the other end tags are ignored and no cycle is formed. Tags that are
    left open stay open to the end.

    The style is computed as the tags are opened and closed, so the cost
    does not grow with the nesting of the tags.

    :param raw_text: the cue text with its cue tags
    :param rules: the rules of the styles
    :returns: the text of each fragment, with its character references as
        written, and the style of the fragment
    """
    # the style of the cue, with the rules of ::cue, which match the root
    values = dict(_INITIAL_STYLE)
    values.update(_declared(rules, _ROOT))
    # the open tags, each with the style before it was opened
    stack: list[tuple[_Element, dict[str, str]]] = []
    for token, is_tag in _iter_cue_text_tokens(raw_text):
        if not is_tag:
            yield token, values
            continue
        tag = _tag(token)
        if tag is None:
            continue
        kind, element = tag
        if kind == 'start':
            if _can_open(stack, element):
                stack.append((element, values))
                values = _open(values, element, rules)
        else:
            values = _close(stack, element, values)


def _can_open(stack: Sequence[tuple[_Element, dict[str, str]]],
              element: _Element) -> bool:
    """
    Return whether a start tag opens a tag.

    :param stack: the open tags, each with the style before it was opened
    :param element: the element of the start tag
    :returns: true unless the tag is an ``rt`` tag outside a ``ruby`` tag,
        which WebVTT reads as text
    """
    return (element.name != 'rt' or
            (bool(stack) and stack[-1][0].name == 'ruby'))


def _close(
        stack: list[tuple[_Element, dict[str, str]]],
        element: _Element,
        values: dict[str, str]
        ) -> dict[str, str]:
    """
    Close a cue tag, as in the WebVTT cue text parsing rules.

    An end tag only closes the tag that it names when it is the innermost
    one, and an end tag of ruby also closes a ruby tag through its rt tag.
    Other end tags are ignored.

    :param stack: the open tags, each with the style before it was opened,
        which are changed in place
    :param element: the element of the end tag
    :param values: the style of the text at the tag
    :returns: the style of the text after the tag
    """
    if stack and stack[-1][0].name == element.name:
        return stack.pop()[1]
    if (element.name == 'ruby' and len(stack) > 1 and
            stack[-1][0].name == 'rt' and stack[-2][0].name == 'ruby'):
        stack.pop()
        return stack.pop()[1]
    return values


def cue_styles(
        caption: Caption,
        styles: Iterable[Style] = ()
        ) -> list[CueStyle]:
    """
    Return the style of each fragment of a cue.

    The fragments are the text between the cue tags of ``raw_text``, in
    order, each with the tags that contain it. Their style is evaluated as
    described in the module, with the rules of the styles and the default
    styles of WebVTT. Fragments without text are left out.

    Evaluating the styles never raises for CSS that is not valid, and the
    ``STYLE`` blocks and the caption are left unchanged.

    :param caption: the caption to evaluate
    :param styles: the styles of the file, in their order
    :returns: the style of each fragment, in the order of the text

    :raises TypeError: if caption is not a `Caption` or a style is not a
        `Style`
    :raises ValueError: if a style sheet nests rules deeper than
        `MAX_NESTING`
    """
    if not isinstance(caption, Caption):
        raise TypeError(f'Expected a Caption, got {caption!r}')
    rules = _rules(styles)

    return [
        _cue_style(text, values)
        for text, values in _fragments(caption.raw_text, rules)
        if text
        ]


def _cue_style(text: str, values: dict[str, str]) -> CueStyle:
    """
    Return the style of a fragment of cue text.

    :param text: the text of the fragment
    :param values: the value of each property of the fragment
    :returns: the style of the fragment
    """
    return CueStyle(
        text,
        color=values['color'],
        background_color=values['background-color'],
        font_style=values['font-style'],
        font_weight=values['font-weight'],
        font_variant=values['font-variant'],
        text_decoration=values['text-decoration'],
        )
