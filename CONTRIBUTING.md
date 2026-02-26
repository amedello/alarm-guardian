# Contributing to Alarm Guardian

Thank you for your interest in contributing!

## Development setup

1. Fork and clone the repository
2. Copy `custom_components/alarm_guardian` to your HA `/config/custom_components/`
3. Restart Home Assistant

## Reporting bugs

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) and include:
- Home Assistant version
- Alarm Guardian version
- Relevant log entries (Settings → System → Logs, filter by `alarm_guardian`)
- Steps to reproduce

## Submitting changes

1. Create a branch from `main`: `git checkout -b fix/my-fix`
2. Make your changes
3. Verify syntax: `python3 -m py_compile custom_components/alarm_guardian/*.py`
4. Update `CHANGELOG.md` under `[Unreleased]`
5. Open a pull request against `main`

## Code style

- Follow existing patterns in the codebase
- All user-facing strings must be in `strings.json` and all 5 translation files
- New config flow steps need entries in both `config` and `options` sections of translations
- Log messages in English

## Adding a language

1. Copy `custom_components/alarm_guardian/translations/en.json`
2. Rename to the [BCP 47 language tag](https://www.iana.org/assignments/language-subtag-registry) (e.g. `nl.json`)
3. Translate all values (keep all keys unchanged)
4. Open a pull request
