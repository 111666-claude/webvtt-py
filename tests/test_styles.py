"""Tests for the evaluation of the styles of the cues."""

import itertools
import pathlib
import textwrap
import unittest

import webvtt
from webvtt import CueStyle, Style, cue_styles
from webvtt.models import Caption
from webvtt.styles import MAX_NESTING

PATH_TO_SAMPLES = pathlib.Path(__file__).resolve().parent / 'samples'

WHITE = 'rgba(255, 255, 255, 1)'


def _caption(text):
    return Caption(text=text)


def _style(text, *css):
    return cue_styles(_caption(text), [Style(item) for item in css])


def _colours(text, *css):
    return [fragment.color for fragment in _style(text, *css)]


def _fragments(text, *css):
    return [(fragment.text, fragment.color)
            for fragment in _style(text, *css)]


class TestDefaultStyles(unittest.TestCase):

    def test_text_without_tags(self):
        fragments = _style('Hello there')
        self.assertEqual(
            fragments,
            [CueStyle('Hello there',
                      color=WHITE,
                      background_color='transparent',
                      font_style='normal',
                      font_weight='normal',
                      font_variant='normal',
                      text_decoration='none'
                      )]
            )

    def test_bold_italic_underline(self):
        fragments = _style('<b>bold</b> <i>italic</i> <u>underlined</u>')
        self.assertEqual(
            [(fragment.text, fragment.font_weight, fragment.font_style,
              fragment.text_decoration) for fragment in fragments],
            [('bold', 'bold', 'normal', 'none'),
             (' ', 'normal', 'normal', 'none'),
             ('italic', 'normal', 'italic', 'none'),
             (' ', 'normal', 'normal', 'none'),
             ('underlined', 'normal', 'normal', 'underline')]
            )

    def test_default_colour_classes(self):
        for name, colour in (
                ('white', WHITE),
                ('lime', 'rgba(0, 255, 0, 1)'),
                ('cyan', 'rgba(0, 255, 255, 1)'),
                ('red', 'rgba(255, 0, 0, 1)'),
                ('yellow', 'rgba(255, 255, 0, 1)'),
                ('magenta', 'rgba(255, 0, 255, 1)'),
                ('blue', 'rgba(0, 0, 255, 1)'),
                ('black', 'rgba(0, 0, 0, 1)')):
            with self.subTest(name=name):
                self.assertEqual(_colours(f'<c.{name}>x</c>'), [colour])

    def test_default_background_classes(self):
        fragment = _style('<c.bg_white>x</c>')[0]
        self.assertEqual(fragment.background_color, WHITE)

    def test_last_default_class_of_each_kind_applies(self):
        fragment = _style('<c.yellow.magenta.bg_red.bg_blue>x</c>')[0]
        self.assertEqual(fragment.color, 'rgba(255, 0, 255, 1)')
        self.assertEqual(fragment.background_color, 'rgba(0, 0, 255, 1)')

    def test_colour_classes_apply_to_any_tag_with_them(self):
        # the presentation hints of the classes apply to the text of the
        # tag, as in WebVTT, also in a voice or a language span
        self.assertEqual(_colours('<v.loud Esme>x</v>'),
                         ['rgba(255, 255, 255, 1)'])
        self.assertEqual(_colours('<v.red Esme>x</v>'),
                         ['rgba(255, 0, 0, 1)'])
        self.assertEqual(_colours('<lang.yellow en>x</lang>'),
                         ['rgba(255, 255, 0, 1)'])

    def test_empty_fragments_are_left_out(self):
        self.assertEqual(_style('<b></b>text<b></b>'), _style('text'))
        self.assertEqual(_style('<b><i>text</i></b>')[-1].text, 'text')
        self.assertEqual([fragment.text for fragment in _style('a\n\nb')],
                         ['a\n\nb'])

    def test_character_references_are_as_written(self):
        self.assertEqual(_style('2 &lt; 3')[0].text, '2 &lt; 3')


class TestCueTags(unittest.TestCase):

    def test_nested_tags_inherit(self):
        fragments = _style('<b><i>both</i></b>')
        self.assertEqual(
            [(fragment.font_weight, fragment.font_style)
             for fragment in fragments],
            [('bold', 'italic')]
            )

    def test_classes_of_c_tag(self):
        self.assertEqual(_style('<c.loud>x</c>')[0].text, 'x')

    def test_end_tag_of_an_outer_tag_does_not_close_inner_tags(self):
        # as in the WebVTT parsing rules, an end tag only closes the
        # innermost tag when it names it
        fragments = _style('<b><i>both</b> italic')
        self.assertEqual(
            [(fragment.text, fragment.font_weight, fragment.font_style)
             for fragment in fragments],
            [('both', 'bold', 'italic'), (' italic', 'bold', 'italic')]
            )

    def test_end_tag_without_start_tag_is_ignored(self):
        self.assertEqual(
            [(fragment.text, fragment.font_weight)
             for fragment in _style('<b>bold</i></b>')],
            [('bold', 'bold')]
            )

    def test_unclosed_tags_keep_their_style(self):
        self.assertEqual(
            [(fragment.text, fragment.font_weight, fragment.font_style)
             for fragment in _style('<b>bold <i>both')],
            [('bold ', 'bold', 'normal'), ('both', 'bold', 'italic')]
            )

    def test_unknown_tags_are_ignored_keeping_their_text(self):
        fragments = _style('a<ruby>漢<rt>kan</rt></ruby>b')
        self.assertEqual([fragment.text for fragment in fragments],
                         ['a', '漢', 'kan', 'b'])
        self.assertTrue(all(fragment.font_weight == 'normal'
                            for fragment in fragments))

    def test_timestamp_tags_are_ignored_keeping_their_text(self):
        self.assertEqual(_fragments('a <00:00:01.000>b'),
                         [('a ', WHITE), ('b', WHITE)])

    def test_tags_across_line_breaks(self):
        caption = Caption(text='<b>line 1\nline 2</b>')
        self.assertEqual(
            [(fragment.text, fragment.font_weight)
             for fragment in cue_styles(caption)],
            [('line 1\nline 2', 'bold')]
            )

    def test_tag_names_are_not_case_sensitive(self):
        self.assertEqual(_style('<B>x</B>')[0].font_weight, 'bold')

    def test_class_names_keep_their_case(self):
        self.assertEqual(_colours('<c.loud>x</c>',
                                  '::cue(.loud) { color: red; }'), ['red'])
        self.assertEqual(_colours('<c.LOUD>x</c>',
                                  '::cue(.loud) { color: red; }'), [WHITE])

    def test_voice_and_language_tags_do_not_style_the_text(self):
        self.assertEqual(_colours('<v Esme>x</v><lang en>y</lang>'),
                         [WHITE, WHITE])

    def test_text_between_tags_is_each_a_fragment(self):
        self.assertEqual([fragment.text for fragment in _style(
            'one<b>two</b>three')], ['one', 'two', 'three'])

    def test_ruby_tags(self):
        fragments = _style('<ruby>漢<rt>kan</rt></ruby>')
        self.assertEqual([fragment.text for fragment in fragments],
                         ['漢', 'kan'])
        self.assertEqual(_colours('<ruby>漢<rt>kan</rt></ruby>',
                                  '::cue(rt) { color: red; }'),
                         [WHITE, 'red'])

    def test_rt_tag_outside_a_ruby_tag_is_read_as_text(self):
        self.assertEqual(_style('<rt>x</rt>')[0].text, 'x')

    def test_ruby_end_tag_closes_through_rt(self):
        fragments = cue_styles(
            Caption(text='<ruby><rt>a</rt>b'),
            [Style('::cue(rt) { color: red; }')]
            )
        self.assertEqual([(fragment.text, fragment.color)
                          for fragment in fragments],
                         [('a', 'red'), ('b', WHITE)])
        # the end tag of ruby closes the ruby tag through its rt tag
        fragments = cue_styles(
            Caption(text='<ruby>a<rt>b</ruby>c'),
            [Style('::cue(rt) { color: red; }')]
            )
        self.assertEqual([(fragment.text, fragment.color)
                          for fragment in fragments],
                         [('a', WHITE), ('b', 'red'), ('c', WHITE)])

    def test_end_tag_of_a_tag_that_is_not_open_is_ignored(self):
        self.assertEqual(_fragments('<b>a</i>b</b>'),
                         [('a', WHITE), ('b', WHITE)])
        self.assertEqual(_style('<b>a</i>b</b>')[1].font_weight, 'bold')

    def test_voice_without_annotation(self):
        self.assertEqual(
            _colours('<v>x</v>', '::cue(v[voice=""]) { color: red; }'),
            [WHITE]
            )
        self.assertEqual(_colours('<v>x</v>', '::cue(v) { color: red; }'),
                         ['red'])


class TestSelectors(unittest.TestCase):

    def test_cue(self):
        self.assertEqual(_colours('<b>x</b>', '::cue { color: red; }'),
                         ['red'])

    def test_cue_type(self):
        self.assertEqual(_colours('<b>x</b> <i>y</i>',
                                  '::cue(b) { color: red; }'),
                         ['red', WHITE, WHITE])

    def test_cue_class(self):
        self.assertEqual(_colours('<c.loud>x</c> <c.quiet>y</c>',
                                  '::cue(.loud) { color: red; }'),
                         ['red', WHITE, WHITE])

    def test_cue_type_and_class(self):
        self.assertEqual(_colours('<b.loud>x</b> <b>y</b>',
                                  '::cue(b.loud) { color: red; }'),
                         ['red', WHITE, WHITE])

    def test_cue_universal_selector(self):
        # the universal selector matches every internal node object, also
        # the root of the cue
        self.assertEqual(_colours('<b>x</b> <c.y>y</c>',
                                  '::cue(*) { color: red; }'),
                         ['red', 'red', 'red'])
        self.assertEqual(_colours('<b>x</b>',
                                  '::cue(*) { color: red; }'),
                         ['red'])

    def test_cue_more_than_one_class(self):
        self.assertEqual(_colours('<b.loud.red>x</b>',
                                  '::cue(.loud.red) { color: red; }'),
                         ['red'])
        self.assertEqual(_colours('<b.loud>x</b>',
                                  '::cue(.loud.red) { color: red; }'),
                         [WHITE])

    def test_cue_voice(self):
        styles = ['::cue(v[voice="Esme"]) { color: cyan; }']
        self.assertEqual(_colours('<v Esme>x</v> <v Mary>y</v>', *styles),
                         ['cyan', WHITE, WHITE])

    def test_cue_voice_quotes(self):
        for quotes in ('"Esme"', "'Esme'", 'Esme'):
            with self.subTest(quotes=quotes):
                self.assertEqual(
                    _colours('<v Esme>x</v>',
                             f'::cue(v[voice={quotes}]) {{ color: cyan; }}'),
                    ['cyan']
                    )

    def test_cue_voice_with_class(self):
        styles = ['::cue(v.loud[voice="Esme"]) { color: cyan; }']
        self.assertEqual(_colours('<v.loud Esme>x</v>', *styles), ['cyan'])
        self.assertEqual(_colours('<v Esme>x</v>', *styles), [WHITE])

    def test_cue_voice_without_case_flag(self):
        self.assertEqual(
            _colours('<v Esme>x</v>',
                     '::cue(v[voice="esme"]) { color: cyan; }'),
            [WHITE]
            )

    def test_cue_voice_with_case_flag(self):
        self.assertEqual(
            _colours('<v Esme>x</v>',
                     '::cue(v[voice="esme" i]) { color: cyan; }'),
            ['cyan']
            )

    def test_cue_language(self):
        styles = ['::cue(lang[lang="en-US"]) { color: lime; }']
        self.assertEqual(_colours('<lang en-US>x</lang>', *styles), ['lime'])
        self.assertEqual(_colours('<lang en-GB>x</lang>', *styles), [WHITE])

    def test_voice_annotation_is_normalized(self):
        self.assertEqual(
            _colours('<v  Homer \t Simpson >x</v>',
                     '::cue(v[voice="Homer Simpson"]) { color: cyan; }'),
            ['cyan']
            )

    def test_css_escapes(self):
        self.assertEqual(_colours('<c.c+1>x</c>',
                                  r'::cue(.c\+1) { color: red; }'), ['red'])
        self.assertEqual(_colours('<c.1a>x</c>',
                                  r'::cue(.\31 a) { color: red; }'), ['red'])
        self.assertEqual(
            _colours('<v Esmé>x</v>',
                     r'::cue(v[voice="Esm\e9"]) { color: red; }'),
            ['red']
            )

    def test_invalid_css_escapes(self):
        for escape in (r'\0 ', r'\d800 ', r'\110000 '):
            with self.subTest(escape=escape):
                self.assertEqual(
                    _colours('<c.x>x</c>',
                             f'::cue(.{escape}x) {{ color: red; }}'),
                    [WHITE]
                    )

    def test_css_escapes_in_tag_classes(self):
        # WebVTT does not read escapes in cue tags, so the class is the
        # text as written, which a CSS escape does not match
        self.assertEqual(
            _colours('<c.c\\+1>x</c>', r'::cue(.c\+1) { color: red; }'),
            [WHITE]
            )
        self.assertEqual(
            _colours('<c.loud>x</c>', r'::cue(.\6c oud) { color: red; }'),
            ['red']
            )

    def test_selector_lists(self):
        self.assertEqual(_colours('<b>x</b><i>y</i>',
                                  '::cue(b), ::cue(i) { color: red; }'),
                         ['red', 'red'])

    def test_compound_selectors_are_not_read(self):
        for selector in ('::cue(c > b)', '::cue(c b)', '::cue(c+b)'):
            with self.subTest(selector=selector):
                self.assertEqual(
                    _colours('<c><b>x</b></c>',
                             f'{selector} {{ color: red; }}'),
                    [WHITE]
                    )

    def test_other_selectors_are_not_read(self):
        for selector in ('::cue(#id)', '::cue(*:not(:root))',
                         '::cue(b:not(i))', '::cue(:lang(en))',
                         '::cue(b[foo=bar])', '::cue(b[voice~="x"])'):
            with self.subTest(selector=selector):
                self.assertEqual(
                    _colours('<c><b>x</b></c>',
                             f'{selector} {{ color: red; }}'),
                    [WHITE]
                    )

    def test_pseudo_elements_and_classes_after_cue_are_read(self):
        # the properties apply to the cue tags, not to the pseudo-elements
        self.assertEqual(_colours('<b>x</b>', '::cue(b):past { color: red; }'),
                         ['red'])
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b)::before { color: red; }'),
            ['red']
            )

    def test_element_before_cue_is_not_read(self):
        self.assertEqual(_colours('<b>x</b>', 'video::cue(b) { color: red; }'),
                         [WHITE])

    def test_rule_with_a_selector_not_read_is_ignored(self):
        # as in CSS, a rule with a selector that is not valid is left out
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b), ::cue(#id) { color: red; }'),
            [WHITE]
            )

    def test_not_selector_after_cue_is_not_read(self):
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b):not(i) { color: red; }'),
            [WHITE]
            )

    def test_unclosed_selector_is_not_read(self):
        self.assertEqual(_colours('<b>x</b>', '::cue(b { color: red; }'),
                         [WHITE])

    def test_empty_selector_is_not_read(self):
        self.assertEqual(_colours('<b>x</b>', '::cue() { color: red; }'),
                         [WHITE])

    def test_universal_selector_with_pseudo_class_is_not_read(self):
        self.assertEqual(
            _colours('<b>x</b>', '::cue(*:not(x)) { color: red; }'),
            [WHITE]
            )

    def test_attribute_name_that_is_not_read(self):
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b[lang=en]) { color: red; }'),
            [WHITE]
            )

    def test_attribute_of_another_name(self):
        self.assertEqual(
            _colours('<v esme>x</v>', '::cue(v[lang=en]) { color: red; }'),
            [WHITE]
            )

    def test_attribute_with_s_flag(self):
        self.assertEqual(
            _colours('<v esme>x</v>',
                     '::cue(v[voice="esme" s]) { color: red; }'),
            ['red']
            )

    def test_attribute_without_a_value(self):
        self.assertEqual(_colours('<v Esme>x</v>',
                                  '::cue(v[voice]) { color: red; }'), [WHITE])

    def test_attribute_with_comparison_other_than_equals(self):
        for comparison in ('^=', '$=', '*=', '|='):
            with self.subTest(comparison=comparison):
                self.assertEqual(
                    _colours(
                        '<v Esme>x</v>',
                        f'::cue(v[voice{comparison}"Es"]) '
                        '{ color: red; }'
                        ),
                    [WHITE]
                    )

    def test_other_text_after_a_supported_selector(self):
        self.assertEqual(_colours('<b>x</b>', '::cue(b) b { color: red; }'),
                         [WHITE])
        self.assertEqual(_colours('<b>x</b>', '::cue(b .x) { color: red; }'),
                         [WHITE])


class TestDefaultsOfTheFile(unittest.TestCase):
    """The cue styles of a file with the default styles of WebVTT."""

    def test_default_colour_classes_of_the_sample(self):
        # the classes of the sample that set a colour and a background
        vtt = webvtt.from_string(textwrap.dedent('''\
            WEBVTT

            00:00:01.000 --> 00:00:02.000
            <c.yellow.bg_black>both</c> <c.lime>one</c>
            '''))
        self.assertEqual(
            [(fragment.text, fragment.color, fragment.background_color)
             for fragment in vtt.cue_styles(0)],
            [('both', 'rgba(255, 255, 0, 1)', 'rgba(0, 0, 0, 1)'),
             (' ', WHITE, 'transparent'),
             ('one', 'rgba(0, 255, 0, 1)', 'transparent')]
            )

    def test_cue_rule_applies_to_the_background_of_every_tag(self):
        # the background is not inherited, so a ::cue rule for it applies
        # to each tag, as it matches every internal node object
        vtt = webvtt.from_string(textwrap.dedent('''\
            WEBVTT

            STYLE
            ::cue {
              background-color: #101010;
            }

            00:00:01.000 --> 00:00:02.000
            plain <b>bold</b>
            '''))
        self.assertEqual(
            [fragment.background_color for fragment in vtt.cue_styles(0)],
            ['#101010', '#101010']
            )


class TestSpecificity(unittest.TestCase):

    def test_more_classes_apply_first(self):
        self.assertEqual(
            _colours('<c.loud>x</c>',
                     '::cue(.loud) { color: red; }',
                     '::cue(*) { color: blue; }'),
            ['red']
            )

    def test_type_name_after_classes(self):
        self.assertEqual(
            _colours('<b.loud>x</b>',
                     '::cue(b) { color: red; }',
                     '::cue(.loud) { color: blue; }'),
            ['blue']
            )

    def test_attribute_counts_like_a_class(self):
        self.assertEqual(
            _colours('<v.loud Esme>x</v>',
                     '::cue(v.loud[voice="Esme"]) { color: red; }',
                     '::cue(.loud.loud2) { color: blue; }'),
            ['red']
            )

    def test_last_rule_of_the_same_specificity_applies(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(b) { color: red; }',
                     '::cue(b) { color: blue; }'),
            ['blue']
            )

    def test_last_rule_in_the_same_style_applies(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(b) { color: red; } ::cue(b) { color: blue; }'),
            ['blue']
            )

    def test_rule_after_a_style_applies(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(.loud) { color: red; } ::cue(b) { color: blue; }',
                     '::cue(b) { color: green; }'),
            ['green']
            )

    def test_earlier_rule_of_higher_specificity_stays(self):
        self.assertEqual(
            _colours('<b.loud>x</b>',
                     '::cue(.loud) { color: red; }',
                     '::cue(b) { color: blue; }'),
            ['red']
            )

    def test_specificity_before_the_order(self):
        self.assertEqual(
            _colours('<c.loud>x</c>',
                     '::cue(.loud) { color: red; }',
                     '::cue { color: blue; }'),
            ['red']
            )

    def test_inherited_rule_from_a_parent_tag(self):
        self.assertEqual(
            _colours('<c.loud>a<b>b</b></c>',
                     '::cue(.loud) { color: red; }',
                     '::cue(b) { color: blue; }'),
            ['red', 'blue']
            )

    def test_cue_rule_applies_to_every_tag(self):
        self.assertEqual(_colours('<b>a<i>b</i></b>', '::cue { color: red; }'),
                         ['red', 'red'])

    def test_duplicate_properties_in_a_rule(self):
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b) { color: red; color: blue; }'),
            ['blue']
            )


class TestDeclarations(unittest.TestCase):

    def test_declarations_are_case_insensitive(self):
        self.assertEqual(_colours('<b>x</b>', '::cue(b) { COLOR: RED; }'),
                         ['RED'])

    def test_important_is_removed(self):
        self.assertEqual(_colours('<b>x</b>',
                                  '::cue(b) { color: red !important; }'),
                         ['red'])

    def test_background_shorthand(self):
        fragment = _style('<b>x</b>',
                          '::cue(b) { background: url(x) no-repeat #123; }'
                          )[0]
        self.assertEqual(fragment.background_color, '#123')

    def test_background_shorthand_without_colour(self):
        fragment = _style('<b>x</b>',
                          '::cue(b) { background: url(x) no-repeat; }')[0]
        self.assertEqual(fragment.background_color, 'transparent')

    def test_background_colour_before_background_shorthand(self):
        fragment = _style(
            '<b>x</b>',
            '::cue(b) { background-color: blue; background: red; }'
            )[0]
        self.assertEqual(fragment.background_color, 'red')

    def test_background_is_not_inherited(self):
        fragments = _style('<c.box>a <b>b</b></c>',
                           '::cue(.box) { background-color: blue; }')
        self.assertEqual(
            [(fragment.text, fragment.background_color)
             for fragment in fragments],
            [('a ', 'blue'), ('b', 'transparent')]
            )

    def test_background_is_not_inherited_and_set_by_the_tag(self):
        fragments = _style('<c.box>a <b.bg_red>b</b></c>',
                           '::cue(.box) { background-color: blue; }')
        self.assertEqual([fragment.background_color for fragment in fragments],
                         ['blue', 'rgba(255, 0, 0, 1)'])

    def test_font_properties_are_inherited(self):
        fragments = _style('<c.box>a <b>b</b></c>',
                           '::cue(.box) { color: red; font-style: italic; }')
        self.assertEqual(
            [(fragment.text, fragment.color, fragment.font_style)
             for fragment in fragments],
            [('a ', 'red', 'italic'), ('b', 'red', 'italic')]
            )

    def test_font_variant(self):
        self.assertEqual(
            _style('<b>x</b>',
                   '::cue(b) { font-variant: small-caps; }')[0].font_variant,
            'small-caps'
            )

    def test_text_decoration(self):
        self.assertEqual(
            _style('<b>x</b>',
                   '::cue(b) { text-decoration: underline dotted; }'
                   )[0].text_decoration,
            'underline dotted'
            )

    def test_colours_are_kept_as_written(self):
        for value in ('red', '#f00', '#ff0000', 'rgba(255, 0, 0, 1)',
                      'hsl(0, 100%, 50%)', 'currentColor', 'transparent'):
            with self.subTest(value=value):
                self.assertEqual(
                    _colours('<b>x</b>', f'::cue(b) {{ color: {value}; }}'),
                    [value]
                    )

    def test_font_weight_numbers(self):
        for value in ('100', '700', '0', '1000', '1001'):
            with self.subTest(value=value):
                self.assertEqual(
                    _style('<b>x</b>',
                           f'::cue(b) {{ font-weight: {value}; }}'
                           )[0].font_weight,
                    {'0': 'bold', '1001': 'bold'}.get(value, value)
                    )


class TestIgnored(unittest.TestCase):

    def test_invalid_colours(self):
        for value in ('notacolour', '"blue"', '<x>', 'rgb(1, 2)', '#12',
                      'rgb(1 2 3 4 5)'):
            with self.subTest(value=value):
                self.assertEqual(
                    _colours('<b>x</b>', f'::cue(b) {{ color: {value}; }}'),
                    [WHITE]
                    )

    def test_invalid_font_values(self):
        for name, value, default in (
                ('font-style', 'slanted', 'normal'),
                ('font-weight', 'heavy', 'bold'),
                ('font-variant', 'big', 'normal'),
                ('text-decoration', 'shiny', 'none')):
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(_style('<b>x</b>',
                                   f'::cue(b) {{ {name}: {value}; }}')[0],
                            name.replace('-', '_')),
                    default
                    )

    def test_unknown_properties(self):
        fragment = _style('<b>x</b>',
                          '::cue(b) { font-size: 3em; color: red; }')[0]
        self.assertEqual(fragment.color, 'red')

    def test_unknown_at_rules_are_skipped(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '@media screen { ::cue(b) { color: red; } }'
                     ' ::cue(b) { color: lime; }'),
            ['lime']
            )
        self.assertEqual(
            _colours('<b>x</b>',
                     '@charset "utf-8"; ::cue(b) { color: lime; }'),
            ['lime']
            )
        self.assertEqual(
            _colours('<b>x</b>',
                     '@import url(";}") screen; ::cue(b) { color: lime; }'),
            ['lime']
            )
        self.assertEqual(_colours('<b>x</b>',
                                  '@media screen { ::cue(b) { color: red; }'),
                         [WHITE])

    def test_rules_that_nest_rules_are_skipped(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(b) { color: red; ::cue(i) { color: blue; } }'),
            [WHITE]
            )

    def test_comments_are_skipped(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '/* ::cue(b) { color: red; } */'
                     ' ::cue(b) { /* color: lime; */ color: green; }'),
            ['green']
            )
        self.assertEqual(_colours('<b>x</b>', '/* ::cue(b) { color: red; }'),
                         [WHITE])

    def test_empty_and_malformed_rules(self):
        for css, expected in (
                ('::cue(b) {}', [WHITE]),
                ('::cue(b) { color: }', [WHITE]),
                ('::cue(b) { : red }', [WHITE]),
                ('::cue(b) { color: red;', [WHITE]),
                ('not a rule', [WHITE]),
                ('::cue(', [WHITE]),
                ('::cue(b) { color: red; } }', ['red']),
                ('}', [WHITE])):
            with self.subTest(css=css):
                self.assertEqual(_colours('<b>x</b>', css), expected)

    def test_declarations_without_a_colon_are_skipped(self):
        self.assertEqual(_colours('<b>x</b>',
                                  '::cue(b) { color red; color: blue; }'),
                         ['blue'])

    def test_style_errors_do_not_raise(self):
        # no style, however malformed, raises
        for index in range(256):
            with self.subTest(index=index):
                cue_styles(_caption('<b>x</b>'), [Style(chr(index) * 3)])

    def test_too_deep_nesting_raises(self):
        css = ('::cue(b) { ' * (MAX_NESTING + 1) + 'color: red;' +
               ' }' * (MAX_NESTING + 1))
        with self.assertRaisesRegex(ValueError, 'nested deeper'):
            cue_styles(_caption('<b>x</b>'), [Style(css)])

    def test_at_rule_nested_too_deep_raises(self):
        css = '@media screen { ' * (MAX_NESTING + 1) + '} ' * (
            MAX_NESTING + 1)
        with self.assertRaisesRegex(ValueError, 'nested deeper'):
            cue_styles(_caption('<b>x</b>'), [Style(css)])

    def test_escaped_brace_and_quote_in_css(self):
        # an escaped brace, as in a class name, does not close a block
        self.assertEqual(
            _colours('<b>x</b>', r'::cue(b) { content: "\{"; color: red; }'),
            ['red']
            )
        self.assertEqual(
            _colours('<b>x</b>',
                     '@media \\{ screen; ::cue(b) { color: red; }'),
            ['red']
            )
        self.assertEqual(
            _colours('<b>x</b>', '@media "}" { ::cue(b) { color: red; } }'),
            [WHITE]
            )
        # a brace in quotes does not close a block
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b) { content: "}"; color: red; }'),
            ['red']
            )

    def test_at_rule_without_an_end_skips_the_rest(self):
        self.assertEqual(_colours('<b>x</b>', '@media screen "x"'),
                         [WHITE])
        self.assertEqual(_colours('<b>x</b>', '@media screen "x'),
                         [WHITE])
        self.assertEqual(_colours('<b>x</b>',
                                  '@media screen } ::cue(b) { color: red; }'),
                         ['red'])

    def test_unclosed_rule_after_a_rule_ends_the_read(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(b) { color: red; } ::cue(b) { color: blue;'),
            ['red']
            )

    def test_colour_function_with_an_invalid_component(self):
        self.assertEqual(
            _colours('<b>x</b>', '::cue(b) { color: rgb(1, 2, red?); }'),
            [WHITE]
            )

    def test_voice_and_language_without_an_annotation(self):
        self.assertEqual(_colours('<v >x</v>', '::cue(v) { color: red; }'),
                         ['red'])
        self.assertEqual(_colours('<lang>x</lang>',
                                  '::cue(lang) { color: red; }'),
                         ['red'])


class TestInputs(unittest.TestCase):

    def test_style_blocks_are_not_changed(self):
        style = Style('::cue(.loud) { color: red; }')
        lines = list(style.lines)
        cue_styles(_caption('<c.loud>x</c>'), [style])
        self.assertEqual(style.lines, lines)
        self.assertEqual(style.text, '::cue(.loud) { color: red; }')

    def test_caption_is_not_changed(self):
        caption = _caption('<b>x</b>')
        lines = list(caption.lines)
        cue_styles(caption, [Style('::cue(b) { color: red; }')])
        self.assertEqual(caption.lines, lines)
        self.assertEqual(caption.raw_text, '<b>x</b>')

    def test_wrong_caption_type(self):
        for value in ('<b>x</b>', None, 1):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    cue_styles(value)  # type: ignore[arg-type]

    def test_wrong_style_type(self):
        with self.assertRaises(TypeError):
            cue_styles(_caption('x'),
                       ['::cue { color: red; }']  # type: ignore[list-item]
                       )

    def test_no_styles_uses_the_defaults(self):
        self.assertEqual(_fragments('<b>x</b>'), [('x', WHITE)])

    def test_style_sheets_are_read_in_their_order(self):
        self.assertEqual(
            _colours('<b>x</b>',
                     '::cue(b) { color: red; }',
                     '::cue(b) { color: blue; }',
                     '::cue(b) { color: green; }'),
            ['green']
            )


class TestWebVTT(unittest.TestCase):

    def test_cue_styles_by_index(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        self.assertEqual(vtt.cue_styles(0), cue_styles(vtt[0], vtt.styles))

    def test_cue_styles_by_caption(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        self.assertEqual(vtt.cue_styles(vtt[1]),
                         cue_styles(vtt[1], vtt.styles))

    def test_cue_styles_wrong_index(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        with self.assertRaises(IndexError):
            vtt.cue_styles(len(vtt))

    def test_cue_styles_wrong_type(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        for value in ('0', None, 1.0, True):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    vtt.cue_styles(value)  # type: ignore[arg-type]

    def test_cue_styles_before_index(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        self.assertEqual(vtt.cue_styles(-1), cue_styles(vtt[-1], vtt.styles))

    def test_styles_are_unchanged_by_evaluation(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles.vtt')
        content = vtt.content
        for caption in vtt:
            vtt.cue_styles(caption)
        self.assertEqual(vtt.content, content)

    def test_styles_of_a_file_without_styles(self):
        vtt = webvtt.read(PATH_TO_SAMPLES / 'cue_styles_without_style.vtt')
        self.assertEqual(vtt.styles, [])
        self.assertTrue(all(fragment.color == WHITE
                            for fragment in vtt.cue_styles(0)))


class TestSamples(unittest.TestCase):

    def _fragments_of(self, name):
        vtt = webvtt.read(PATH_TO_SAMPLES / name)
        return [
            [(fragment.text, fragment.color, fragment.background_color,
              fragment.font_weight) for fragment in vtt.cue_styles(index)]
            for index in range(len(vtt))
            ]

    def test_two_speakers(self):
        self.assertEqual(
            self._fragments_of('cue_styles.vtt'),
            [[('Hello', 'peachpuff', 'transparent', 'bold'),
              (' ', 'cyan', 'transparent', 'normal'),
              ('world', '#ffff00', 'transparent', 'bolder')],
             [('Goodbye', '#808080', 'transparent', 'normal'),
              (' ', 'papayawhip', 'transparent', 'normal'),
              ('friend', 'lime', 'transparent', 'normal')],
             [('yellow on black', '#ffff00', '#000000', 'bolder'),
              (' plain', 'papayawhip', 'transparent', 'normal')]]
            )

    def test_without_style(self):
        self.assertEqual(
            self._fragments_of('cue_styles_without_style.vtt'),
            [[('bold', WHITE, 'transparent', 'bold'),
              (' ', WHITE, 'transparent', 'normal'),
              ('italic', WHITE, 'transparent', 'normal'),
              (' ', WHITE, 'transparent', 'normal'),
              ('underlined', WHITE, 'transparent', 'normal'),
              (' plain', WHITE, 'transparent', 'normal')]]
            )

    def test_samples_are_serialized_unchanged(self):
        for name in ('cue_styles.vtt', 'cue_styles_without_style.vtt'):
            with self.subTest(name=name):
                vtt = webvtt.read(PATH_TO_SAMPLES / name)
                content = (PATH_TO_SAMPLES / name).read_text(
                    encoding='utf-8'
                    )
                self.assertEqual(vtt.content, content)
                for caption in vtt:
                    cue_styles(caption, vtt.styles)
                self.assertEqual(vtt.content, content)


class TestEvaluationIsLinear(unittest.TestCase):

    def test_many_tags(self):
        # tags that are not closed stay open, so a long text of tags does
        # not take time quadratic in its length
        self.assertEqual(len(cue_styles(_caption('<b>x' * 2000))), 2000)

    def test_many_rules(self):
        css = ''.join(f'::cue(.c{index}) {{ color: red; }}\n'
                      for index in range(500))
        self.assertEqual(_colours('<c.c499>x</c>', css), ['red'])

    def test_many_styles(self):
        self.assertEqual(
            _colours('<b>x</b>', *['::cue(b) { color: red; }'] * 100),
            ['red']
            )


class TestDocs(unittest.TestCase):

    def test_usage_example(self):
        vtt = webvtt.from_string(textwrap.dedent('''\
            WEBVTT

            STYLE
            ::cue(.yellow) {
              color: #ffff00;
            }
            ::cue(v[voice="Alice"]) {
              color: white;
            }

            00:00:01.000 --> 00:00:02.000
            <v Bob>Hello <c.yellow>Alice</c></v>
            '''))
        self.assertEqual(
            [(fragment.text, fragment.color)
             for fragment in vtt.cue_styles(0)],
            [('Hello ', WHITE), ('Alice', '#ffff00')]
            )


class TestRobustness(unittest.TestCase):
    """Adversarial and edge-case styles and texts do not raise."""

    def test_random_bytes_in_styles_and_cue_text(self):
        alphabet = '{}()[]:;,./*"\'\\@ <>+~-abc0123'
        for size in (1, 2, 3):
            for chars in itertools.product(alphabet, repeat=size):
                css = ''.join(chars)
                cue_styles(_caption('<b>x</b>'), [Style(css)])
                cue_styles(_caption(css), [Style('::cue(b) { color: red; }')])

    def test_broken_tags_are_text(self):
        for text in ('<b', '<', '<<b>>', '<>', '</>', '< /b>', '<b class>'):
            with self.subTest(text=text):
                self.assertEqual(
                    ''.join(fragment.text for fragment in cue_styles(
                        _caption(text))),
                    _caption(text).text
                    )

    def test_unclosed_style_braces_do_not_raise(self):
        for css in ('::cue(b) {', '::cue(b) { color: red;',
                    '::cue(((((', '::cue(b)) { color: red; }',
                    '::cue(v[voice="x) { color: red; }'):
            with self.subTest(css=css):
                cue_styles(_caption('<b>x</b>'), [Style(css)])

    def test_style_with_only_an_at_rule(self):
        self.assertEqual(_colours('<b>x</b>',
                                  '@import url(x); @media print {}'),
                         [WHITE])
