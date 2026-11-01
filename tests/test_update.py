import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import datasets  # noqa: E402
import update  # noqa: E402

SOURCE = {'upstream-db.raw.json': {'url': 'u', 'sha256': 's'}}


def entries(count, named=0):
    return {('female', f'k{index}'): {**datasets.blank(), 'name': '名' if index < named else ''}
            for index in range(count)}


def prior(code='ja'):
    return {'datasets': {code: {'upstream_release': 'v1', 'sources': {'old': {'url': 'o', 'sha256': 'x'}}}}}


def fail():
    raise ValueError('Japanese source table format changed')


class BuildAllTests(unittest.TestCase):
    def build(self, recipes, previous, manifest=None, **options):
        return update.build_all(recipes, previous, manifest or {}, 'v2', SOURCE, **options)

    def test_a_failed_language_reuses_its_data_and_provenance_while_others_build(self):
        previous = {'en': datasets.encode(entries(1)), 'ja': datasets.encode(entries(2))}
        built = self.build({'en': lambda: (entries(2), {}), 'ja': fail}, previous, prior())
        self.assertEqual(built['en']['data'], datasets.encode(entries(2)))
        self.assertEqual(built['en']['upstream_release'], 'v2')
        self.assertEqual(built['en']['sources'], SOURCE)
        self.assertEqual(built['ja']['data'], previous['ja'])
        self.assertEqual(built['ja']['upstream_release'], 'v1')
        self.assertEqual(list(built['ja']['sources']), ['old'])
        self.assertEqual(built['ja']['failure'], 'ValueError: Japanese source table format changed')

    def test_a_failure_with_nothing_to_reuse_stops_the_run(self):
        with self.assertRaisesRegex(RuntimeError, 'no previous release'):
            self.build({'ja': fail}, {})

    def test_shrinking_more_than_ten_percent_counts_as_a_failure(self):
        previous = {'ja': datasets.encode(entries(100))}
        built = self.build({'ja': lambda: (entries(89), {})}, previous, prior())
        self.assertEqual(built['ja']['data'], previous['ja'])
        self.assertIn('tags shrank', built['ja']['failure'])

    def test_named_tags_shrinking_counts_even_when_the_inventory_holds(self):
        previous = {'ja': datasets.encode(entries(100, named=100))}
        built = self.build({'ja': lambda: (entries(100, named=50), {})}, previous, prior())
        self.assertEqual(built['ja']['data'], previous['ja'])
        self.assertIn('named tags shrank', built['ja']['failure'])

    def test_shrinking_within_the_limit_is_accepted(self):
        previous = {'en': datasets.encode(entries(100))}
        built = self.build({'en': lambda: (entries(90), {})}, previous)
        self.assertEqual(built['en']['data'], datasets.encode(entries(90)))
        self.assertIsNone(built['en']['failure'])

    def test_an_accepted_shrink_publishes(self):
        previous = {'en': datasets.encode(entries(100))}
        built = self.build({'en': lambda: (entries(50), {})}, previous, accept_shrink=True)
        self.assertEqual(built['en']['data'], datasets.encode(entries(50)))
        self.assertIsNone(built['en']['failure'])


class ManifestTests(unittest.TestCase):
    def test_describes_each_dataset(self):
        data = datasets.encode(entries(2, named=1))
        built = {'ja': {'data': data, 'upstream_release': 'v1', 'sources': SOURCE, 'failure': 'broken'}}
        manifest = update.build_manifest('v2', built)
        self.assertEqual(manifest['upstream_release'], 'v2')
        self.assertEqual(manifest['datasets']['ja'], {
            'file': 'ja.json', 'sha256': update.digest(data), 'tags': 2, 'named tags': 1,
            'upstream_release': 'v1', 'sources': SOURCE, 'failure': 'broken',
        })


class LoadUpstreamTests(unittest.TestCase):
    URL = update.UPSTREAM_ASSET_PREFIX + 'v1/db.raw.json'

    def load(self, data, license_text=None, url=URL, published=None):
        release = {'tag_name': 'v1', 'assets': [{
            'name': 'db.raw.json', 'browser_download_url': url,
            'digest': published or f'sha256:{update.digest(data)}',
        }]}
        license_text = license_text if license_text is not None else update.UPSTREAM_LICENSE.read_bytes()

        def fetch(requested):
            return license_text if requested.endswith('LICENSE.md') else data

        with patch.object(update, 'github_json', return_value=release), patch.object(update, 'fetch', fetch):
            return update.load_upstream()

    @staticmethod
    def database(count):
        return json.dumps({'data': [
            {'namespace': 'female', 'data': {f'k{index}': {'name': ''} for index in range(count)}}
        ]}).encode()

    def test_accepts_a_verified_release(self):
        data = self.database(update.MIN_UPSTREAM_ENTRIES)
        release, source, database = self.load(data)
        self.assertEqual(release, 'v1')
        self.assertEqual(source['upstream-db.raw.json']['sha256'], update.digest(data))
        self.assertEqual(len(database['data'][0]['data']), update.MIN_UPSTREAM_ENTRIES)

    def test_stops_on_an_unexpected_url_digest_license_or_inventory(self):
        data = self.database(update.MIN_UPSTREAM_ENTRIES)
        with self.assertRaisesRegex(ValueError, 'asset URL'):
            self.load(data, url='https://example.com/db.raw.json')
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.load(data, published='sha256:other')
        with self.assertRaisesRegex(ValueError, 'license'):
            self.load(data, license_text=b'changed')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.load(self.database(update.MIN_UPSTREAM_ENTRIES - 1))


class LoadPreviousTests(unittest.TestCase):
    def test_a_repository_with_no_release_has_no_previous_data(self):
        with patch.object(update, 'latest_release', return_value=None):
            self.assertEqual(update.load_previous('o/r', 'unused'), ({}, {}))

    def test_separates_the_manifest_from_the_datasets(self):
        def download(command, check):
            directory = Path(command[command.index('--dir') + 1])
            (directory / 'en.json').write_bytes(b'data')
            (directory / update.MANIFEST_NAME).write_text('{"upstream_release":"v1"}')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(update, 'latest_release', return_value={'tag_name': 'v1'}), \
                patch.object(update.subprocess, 'run', side_effect=download):
            assets, manifest = update.load_previous('o/r', directory)
        self.assertEqual(assets, {'en': b'data'})
        self.assertEqual(manifest, {'upstream_release': 'v1'})


if __name__ == '__main__':
    unittest.main()
