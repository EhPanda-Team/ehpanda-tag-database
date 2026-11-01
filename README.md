# EhPanda Tag Database

Tag translations and searchable tag indexes for [EhPanda](https://github.com/EhPanda-Team/EhPanda), published
as one release with one file per language.

| File | Language | Content |
|---|---|---|
| `en.json` | English | Tag index with empty translation fields, so the app shows the original tags |
| `ja.json` | Japanese | Curated translations plus every current tag key |
| `ko.json` | Korean | Translated names plus every current tag key |
| `zh-Hans.json` | Simplified Chinese | The upstream database |
| `zh-Hant.json` | Traditional Chinese | The upstream database converted to Taiwanese usage |

Every file has the same shape as the upstream `db.raw.json`, reduced to its tags: compact JSON with
upstream's metadata and `rows` notes removed, and empty `intro` and `links` fields omitted.
Each release also carries `sources.json`, which records the source URLs and SHA-256 hashes behind it.

## How updates work

A workflow runs daily. It fetches every source, builds all five files, and publishes a release only when a file
changed. The app compares each asset's digest, so a change in one language downloads nothing for the others.

- A problem with upstream or its license stops the run, and the last release stays live.
- A problem with a single dataset, including tags or named tags shrinking by more than 10%, keeps that language's
  previous data. The other languages still publish, and the run ends as failed so the problem gets noticed.
- When a drop turns out to be legitimate, run the workflow by hand with `accept_shrink` enabled.
- Releases are uploaded as drafts and verified before going live. The latest seven are kept.
- Each release takes its tag from the upstream release it was built on. If another source changes while upstream
  stays the same, the next one adds a number, as in `v7.29530.1-2`.
- `state/latest.json` records the latest release in git.

Dependencies update on their own when the data stays the same. A weekly workflow covers Python, which Dependabot
cannot: it takes the newest version that `setup-python` offers and OpenCC has a wheel for. Another weekly workflow
takes Dependabot's OpenCC pull requests. Both run the tests and build every file before and after the update.
Identical output goes straight to `main`. Changed output waits in a pull request for a person, because the next
daily run publishes what the update builds. Dependabot's GitHub Actions pull requests go to `main` once the
`Validate` workflow passes on the new versions, and wait for a person if it fails.

To correct a Traditional Chinese conversion, add the tag to `overrides/zh-Hant.json`. Japanese translations that
did not come from the sources below live in `seeds/ja.json`.

## Sources and licenses

These notices do not relicense upstream material. The source and license apply per file.

**All files** derive their tag inventory from
[EhTagTranslation/Database](https://github.com/EhTagTranslation/Database), licensed under Creative Commons
Attribution-NonCommercial-ShareAlike 3.0 China Mainland. The original license text is preserved in
`licenses/EhTagTranslation-Database-LICENSE.md`. `zh-Hans.json` and `zh-Hant.json` also carry its translations,
descriptions and links, under the same license.

**`zh-Hant.json`** is converted with [OpenCC](https://github.com/BYVoid/OpenCC) (Apache License 2.0) using the
`s2twp` configuration. Names, descriptions and link labels are converted; link targets are left unchanged, and
`overrides/zh-Hant.json` replaces individual names.

**`ja.json`** takes attribute translations from [e-hentaidb.com](https://e-hentaidb.com/tag/) under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Existing Japanese
translations in `seeds/ja.json` are distributed under CC BY 4.0 and also credit the
[E(x)Hentai Japanese tag userscript by Nu](https://sleazyfork.org/scripts/391216). Modifications preserve those
translations, apply exact-key attribute translations, and add missing tag keys with empty translation fields.

**`ko.json`** derives names from [Project Violet's Tag Info](https://github.com/project-violet/tag-info), distributed
under the Unlicense and preserved in `licenses/project-violet-tag-info-LICENSE`. Modifications map explicit source
namespaces to matching tag keys, select the first name when aliases are supplied, and leave unavailable
translations empty.

**`en.json`** preserves upstream tag keys with empty translation fields.
