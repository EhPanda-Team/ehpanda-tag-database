import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import datasets  # noqa: E402


def upstream(**namespaces):
    return {'repo': 'ignored', 'data': [
        {'namespace': namespace, 'frontMatters': {}, 'count': len(tags),
         'data': {key: {'name': name, 'intro': '', 'links': ''} for key, name in tags.items()}}
        for namespace, tags in namespaces.items()
    ]}


def japanese_page(rows, license_marker=datasets.JAPANESE_LICENSE_MARKER):
    cells = ''.join(f'<tr><td>{key}</td><td>{name}</td><td></td><td></td><td></td><td></td></tr>'
                    for key, name in rows)
    return f'<a href="https://{license_marker}">license</a><table>{cells}</table>'


class EncodingTests(unittest.TestCase):
    def test_drops_upstream_notes_and_metadata(self):
        entries = datasets.flatten(upstream(rows={'female': '女性'}, female={'glasses': '眼镜'}))
        self.assertEqual(list(entries), [('female', 'glasses')])

    def test_omits_empty_optional_fields_but_keeps_name(self):
        encoded = datasets.encode({('female', 'a'): datasets.blank()})
        self.assertEqual(json.loads(encoded)['data'][0]['data']['a'], {'name': ''})

    def test_round_trip_is_deterministic_regardless_of_order(self):
        first = {('b', 'x'): datasets.blank(), ('a', 'y'): {'name': '名', 'intro': '介', 'links': ''}}
        second = dict(reversed(list(first.items())))
        self.assertEqual(datasets.encode(first), datasets.encode(second))
        self.assertEqual(datasets.flatten(json.loads(datasets.encode(first))), first)

    def test_rejects_malformed_entries(self):
        with self.assertRaises(ValueError):
            datasets.flatten({'data': [{'namespace': 'a', 'data': {'k': {'name': 1}}}]})
        with self.assertRaises(ValueError):
            datasets.flatten({'data': [{'namespace': 'a', 'data': {'k': {'name': ''}}},
                                       {'namespace': 'a', 'data': {'k': {'name': ''}}}]})


class LanguageTests(unittest.TestCase):
    def test_english_keeps_every_key_with_empty_fields(self):
        entries = datasets.english(upstream(female={'glasses': '眼镜'}))
        self.assertEqual(entries, {('female', 'glasses'): datasets.blank()})

    def test_japanese_keeps_seed_adds_keys_and_applies_attribute_names(self):
        rows = [('glasses', '眼鏡'), ('group name', '誤'), ('same', 'same')] + [(f'k{i}', '名') for i in range(500)]
        seed = {'data': [{'namespace': 'artist', 'data': {'kept': {'name': '残す', 'intro': '説明'}}}]}
        entries = datasets.japanese(
            upstream(female={'glasses': '', 'same': '', 'new': ''}, group={'group name': ''}),
            seed, japanese_page(rows))
        self.assertEqual(entries[('female', 'glasses')]['name'], '眼鏡')
        self.assertEqual(entries[('female', 'same')]['name'], '')
        self.assertEqual(entries[('female', 'new')]['name'], '')
        self.assertEqual(entries[('group', 'group name')]['name'], '')
        self.assertEqual(entries[('artist', 'kept')], {'name': '残す', 'intro': '説明', 'links': ''})

    def test_japanese_keeps_the_seed_value_for_a_key_with_conflicting_names(self):
        rows = [('glasses', '眼鏡'), ('glasses', 'メガネ')] + [(f'k{i}', '名') for i in range(500)]
        seed = {'data': [{'namespace': 'female', 'data': {'glasses': {'name': '既存'}}}]}
        entries = datasets.japanese(upstream(female={'glasses': ''}), seed, japanese_page(rows))
        self.assertEqual(entries[('female', 'glasses')]['name'], '既存')

    def test_japanese_refuses_a_changed_license_or_table(self):
        rows = [(f'k{i}', '名') for i in range(500)]
        with self.assertRaisesRegex(ValueError, 'license'):
            datasets.japanese(upstream(), {'data': []}, japanese_page(rows, license_marker='example.com'))
        with self.assertRaisesRegex(ValueError, 'format'):
            datasets.japanese(upstream(), {'data': []}, japanese_page(rows[:10]))

    def test_korean_maps_explicit_namespaces_only(self):
        dictionary = {f'filler:{i}': '값' for i in range(5000)}
        dictionary.update({f'artist:a{i}': '이름' for i in range(1000)})
        dictionary.update({'tag:female:glasses': 'female:안경|별칭', 'series:show': '작품', 'unknown': '무시'})
        tags = {'artist': {f'a{i}': '' for i in range(1000)},
                'female': {'glasses': ''}, 'parody': {'show': ''}, 'male': {'unknown': ''}}
        entries = datasets.korean(upstream(**tags), dictionary)
        self.assertEqual(entries[('female', 'glasses')]['name'], '안경')
        self.assertEqual(entries[('parody', 'show')]['name'], '작품')
        self.assertEqual(entries[('male', 'unknown')]['name'], '')

    def test_korean_refuses_a_shrunken_source(self):
        with self.assertRaisesRegex(ValueError, 'size'):
            datasets.korean(upstream(female={'a': ''}), {'tag:female:a': '이름'})


class ConversionTests(unittest.TestCase):
    @staticmethod
    def marker(text):
        return f'<{text}>'

    def assert_converted(self, text, expected):
        self.assertEqual(datasets.convert_prose(text, self.marker), expected)

    def test_link_targets_stay_untouched(self):
        self.assert_converted('[维基](https://a/简体) 后文', '<[维基](>https://a/简体<) 后文>')

    def test_targets_with_parentheses_and_titles_stay_untouched(self):
        self.assert_converted('![图](https://a/b_(动画).jpg "简体")文', '<![图](>https://a/b_(动画).jpg "简体"<)文>')

    def test_bare_urls_stop_at_full_width_punctuation(self):
        self.assert_converted('见https://a/简体，后文', '<见>https://a/简体<，后文>')

    def test_unclosed_target_is_protected_to_the_end_of_its_line(self):
        self.assert_converted('[a](https://b/简体\n后文', '<[a](>https://b/简体<\n后文>')

    def test_text_without_links_is_one_piece(self):
        self.assert_converted('简体', '<简体>')
        self.assertEqual(datasets.split_protected(''), [])

    def test_real_converter_uses_taiwan_usage_and_keeps_urls(self):
        import opencc
        convert = opencc.OpenCC('s2twp').convert
        database = upstream(other={'mouse': '鼠标', 'full color': '全彩色'})
        database['data'][0]['data']['mouse'].update(
            intro='软件 ![图](https://a/软件.jpg) https://b/软件', links='[维基百科](https://c/软件)')
        entries = datasets.traditional_chinese(database, convert, {'other': {'full color': '全彩'}})
        self.assertEqual(entries[('other', 'mouse')], {
            'name': '滑鼠',
            'intro': '軟體 ![圖](https://a/软件.jpg) https://b/软件',
            'links': '[維基百科](https://c/软件)',
        })
        self.assertEqual(entries[('other', 'full color')]['name'], '全彩')

    def test_a_converter_that_changes_a_target_fails_the_dataset(self):
        database = upstream(other={'key': '[a](https://b)'})
        with self.assertRaisesRegex(ValueError, 'link target'):
            datasets.traditional_chinese(database, lambda text: text.replace('[a](', '[a](x'), {})


class RepositoryDataTests(unittest.TestCase):
    def test_seed_holds_only_entries_with_content(self):
        entries = datasets.flatten(json.loads((ROOT / 'seeds/ja.json').read_text()))
        self.assertGreater(len(entries), 20000)
        self.assertEqual(entries[('female', 'glasses')]['name'], '眼鏡')
        self.assertTrue(all(any(entry.values()) for entry in entries.values()))

    def test_overrides_are_well_formed(self):
        overrides = json.loads((ROOT / 'overrides/zh-Hant.json').read_text())
        self.assertEqual(overrides['other']['full color'], '全彩')


if __name__ == '__main__':
    unittest.main()
