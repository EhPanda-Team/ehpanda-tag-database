"""Dataset generation: parsed sources in, entries or bytes out, with no network or filesystem access."""
import json
import re
from collections import defaultdict
from html.parser import HTMLParser

FIELDS = ('name', 'intro', 'links')
# `rows` documents upstream's namespaces; it holds no tags and the app ignores it.
NON_TAG_NAMESPACES = {'rows'}
JAPANESE_ATTRIBUTE_NAMESPACES = {'male', 'female', 'mixed', 'other', 'location'}
JAPANESE_LICENSE_MARKER = 'creativecommons.org/licenses/by/4.0'

# A bare URL ends at whitespace, quoting, closing brackets or full-width punctuation.
BARE_URL = re.compile(r'''https?://[^\s"'<>)\]，。、；：！？）】」』]*''')


def flatten(database):
    """Map a database to {(namespace, key): {name, intro, links}} and reject malformed input."""
    entries = {}
    for row in database['data']:
        namespace = row['namespace']
        if not isinstance(namespace, str) or not namespace:
            raise ValueError('Invalid namespace')
        if namespace in NON_TAG_NAMESPACES:
            continue
        for key, value in row['data'].items():
            entry = {field: value.get(field, '') for field in FIELDS}
            if not key or not all(isinstance(text, str) for text in entry.values()):
                raise ValueError(f'Invalid entry {namespace}:{key}')
            if (namespace, key) in entries:
                raise ValueError(f'Duplicate entry {namespace}:{key}')
            entries[(namespace, key)] = entry
    return entries


def encode(entries):
    """Serialize deterministically, so an unchanged dataset keeps its digest and is not downloaded again."""
    namespaces = {}
    for (namespace, key), entry in sorted(entries.items()):
        namespaces.setdefault(namespace, {})[key] = {
            field: text for field, text in entry.items() if text or field == 'name'
        }
    database = {'data': [{'namespace': namespace, 'data': tags} for namespace, tags in namespaces.items()]}
    return (json.dumps(database, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def blank():
    return dict.fromkeys(FIELDS, '')


def english(upstream):
    """Keys only: the app shows the original tag, and the index still powers search and suggestions."""
    return {key: blank() for key in flatten(upstream)}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag == 'td' and self.row is not None:
            self.cell = []
        elif tag == 'br' and self.cell is not None:
            self.cell.append('\n')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def japanese(upstream, seed, html):
    """Keep the curated seed, add every current upstream key, and apply exact-key attribute names.

    A key the source names in conflicting ways keeps its seed value, because guessing could publish a
    wrong translation and one such row should not hold back every other Japanese update.
    """
    if JAPANESE_LICENSE_MARKER not in html:
        raise ValueError('Japanese source no longer declares the reviewed CC BY 4.0 license')
    parser = TableParser()
    parser.feed(html)
    if len(parser.rows) < 500 or any(len(row) != 6 for row in parser.rows):
        raise ValueError('Japanese source table format changed')
    candidates = defaultdict(set)
    for key, name, *_ in parser.rows:
        if key and name:
            candidates[key].add(name)
    entries = {key: dict(entry) for key, entry in flatten(seed).items()}
    for namespace, key in flatten(upstream):
        entry = entries.setdefault((namespace, key), blank())
        names = candidates.get(key, set())
        if namespace in JAPANESE_ATTRIBUTE_NAMESPACES and len(names) == 1 and key not in names:
            entry['name'] = next(iter(names))
    return entries


def korean_names(dictionary, inventory):
    """Map explicitly namespaced keys only; never guess an ambiguous namespace."""
    names = {}
    for source_key, text in dictionary.items():
        if not isinstance(source_key, str) or not isinstance(text, str):
            raise ValueError('Korean source keys and values must be strings')
        key = source_key
        if key.startswith(('tag:female:', 'tag:male:')):
            key = key[4:]
        elif key.startswith('series:'):
            key = 'parody:' + key[7:]
        namespace, separator, tag = key.partition(':')
        pair = (namespace, tag)
        if not separator or pair not in inventory:
            continue
        name = text.split('|', 1)[0].strip()
        if namespace in {'female', 'male'} and name.startswith(namespace + ':'):
            name = name[len(namespace) + 1:].strip()
        if not name or name == tag:
            continue
        if pair in names and names[pair] != name:
            raise ValueError('Conflicting Korean translations for ' + key)
        names[pair] = name
    return names


def korean(upstream, dictionary):
    if not isinstance(dictionary, dict) or len(dictionary) < 5000:
        raise ValueError('Unexpected Korean dictionary size; inspect source before updating')
    entries = {key: blank() for key in flatten(upstream)}
    names = korean_names(dictionary, entries)
    if len(names) < 1000:
        raise ValueError('Unexpected Korean key coverage; inspect source before updating')
    for key, name in names.items():
        entries[key]['name'] = name
    return entries


def link_target_end(text, start):
    """Return the index of the `)` closing the target opened before `start`, or the end of the line."""
    depth = 1
    for index in range(start, len(text)):
        character = text[index]
        if character == '\n':
            return index
        if character == '(':
            depth += 1
        elif character == ')':
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def split_protected(text):
    """Split text into (protected, piece) pairs, where protected pieces are link targets and bare URLs.

    Upstream URLs often contain simplified characters, such as Wikipedia titles and image filenames.
    Converting those would point at pages and images that do not exist. A target that never closes is
    protected up to the end of its line: leaving prose unconverted is cheap, a broken link is not.
    """
    pieces = []
    position = 0
    while position < len(text):
        opening = text.find('](', position)
        prose_end = len(text) if opening < 0 else opening + 2
        prose = text[position:prose_end]
        cursor = 0
        for match in BARE_URL.finditer(prose):
            pieces.append((False, prose[cursor:match.start()]))
            pieces.append((True, match.group()))
            cursor = match.end()
        pieces.append((False, prose[cursor:]))
        if opening < 0:
            break
        target_end = link_target_end(text, prose_end)
        pieces.append((True, text[prose_end:target_end]))
        position = target_end
    return [piece for piece in pieces if piece[1]]


def protected_pieces(text):
    return [piece for protected, piece in split_protected(text) if protected]


def convert_prose(text, convert):
    return ''.join(piece if protected else convert(piece) for protected, piece in split_protected(text))


def traditional_chinese(upstream, convert, overrides):
    """Convert every displayed field to Taiwanese usage.

    Fails if a converted text parses to different link targets, which would mean conversion changed
    the Markdown around them. The targets themselves are reinserted verbatim by `convert_prose`.
    """
    entries = {}
    for (namespace, key), entry in flatten(upstream).items():
        converted = {field: convert_prose(text, convert) for field, text in entry.items()}
        for field, text in entry.items():
            if protected_pieces(text) != protected_pieces(converted[field]):
                raise ValueError(f'Conversion changed a link target in {namespace}:{key}')
        override = overrides.get(namespace, {}).get(key)
        if override is not None:
            converted['name'] = override
        entries[(namespace, key)] = converted
    return entries
