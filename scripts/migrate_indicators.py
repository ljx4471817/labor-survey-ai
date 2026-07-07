"""
Migrate FAQ indicators when the survey system document changes.

Reads a migration_map.json that defines:
  - renamed: {"F10": "F11", ...}   old → new indicator codes
  - removed: ["F42", ...]           deleted indicators
  - added: [{"code": "F99", "description": "..."}, ...]

Workflow:
  1. Update indicators field (precise, no regex false matches)
  2. Regex-replace F-patterns in source/question/answer/keywords
  3. Flag entries with removed indicators for manual review
  4. Validate all indicator codes against indicator_catalog.json
  5. Generate affected-entries report

Usage:
  python scripts/migrate_indicators.py migration_map.json           # dry-run
  python scripts/migrate_indicators.py migration_map.json --write   # apply
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = ROOT / "knowledge-base" / "qa" / "faq.json"
EVAL_PATH = ROOT / "knowledge-base" / "qa" / "eval_set.json"
CATALOG_PATH = ROOT / "knowledge-base" / "indicator_catalog.json"


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build_code_regex(code: str) -> re.Pattern:
    """Build regex to match an indicator code as a word in text."""
    return re.compile(r'\b' + re.escape(code) + r'\b')


def migrate_faq(faq: list, mmap: dict, catalog: dict) -> dict:
    """Migrate indicators in FAQ entries. Returns stats."""
    renamed = mmap.get('renamed', {})
    removed = set(mmap.get('removed', []))
    all_catalog_codes = set()
    for mod in catalog['modules'].values():
        all_catalog_codes.update(mod.keys())

    stats = {
        'renamed_entries': 0,
        'removed_flags': 0,
        'body_replaces': 0,
        'errors': [],
    }
    affected_ids = defaultdict(list)  # change_type → [id, ...]

    for entry in faq:
        indicators = entry.get('indicators', [])
        if not indicators:
            continue

        new_indicators = []
        entry_changed = False

        for code in indicators:
            if code in renamed:
                new_code = renamed[code]
                new_indicators.append(new_code)
                entry_changed = True
                affected_ids[f'renamed: {code}→{new_code}'].append(entry['id'])
            elif code in removed:
                entry['_indicators_removed'] = True
                stats['removed_flags'] += 1
                affected_ids[f'removed: {code}'].append(entry['id'])
            else:
                new_indicators.append(code)

        if entry_changed:
            entry['indicators'] = sorted(set(new_indicators))
            stats['renamed_entries'] += 1

        # Validate all indicators against catalog
        for code in entry.get('indicators', []):
            if code not in all_catalog_codes:
                stats['errors'].append(f"[{entry['id']}] indicator {code} not in catalog")

    # Regex replace in body text (source, question, answer, keywords)
    text_fields = ['source', 'question', 'answer']
    for entry in faq:
        for old_code, new_code in renamed.items():
            pattern = build_code_regex(old_code)
            for field in text_fields:
                if field in entry and pattern.search(entry[field]):
                    entry[field] = pattern.sub(new_code, entry[field])
                    stats['body_replaces'] += 1
            if 'keywords' in entry:
                for i, kw in enumerate(entry['keywords']):
                    if pattern.search(kw):
                        entry['keywords'][i] = pattern.sub(new_code, kw)
                        stats['body_replaces'] += 1

            # Also handle source patterns like "问题10" → "问题11"
            if old_code.startswith('F'):
                num = old_code[1:]
                new_num = new_code[1:] if new_code.startswith('F') else new_code
                problem_pattern = re.compile(r'(问题\s*)' + re.escape(num) + r'\b')
                for field in text_fields:
                    if field in entry and problem_pattern.search(entry[field]):
                        entry[field] = problem_pattern.sub(r'\g<1>' + new_num, entry[field])

    # Clean up review flags that are no longer needed
    for entry in faq:
        if '_indicators_review' in entry and entry.get('indicators'):
            del entry['_indicators_review']

    return stats, affected_ids


def migrate_eval(evals: list, mmap: dict) -> dict:
    """Migrate indicator references in eval set."""
    renamed = mmap.get('renamed', {})
    stats = {'eval_body_replaces': 0}

    text_fields = ['question', 'expected_source_section']
    for item in evals:
        for old_code, new_code in renamed.items():
            pattern = build_code_regex(old_code)
            for field in text_fields:
                if field in item and pattern.search(item[field]):
                    item[field] = pattern.sub(new_code, item[field])
                    stats['eval_body_replaces'] += 1
    return stats


def validate_migration_map(mmap: dict, catalog: dict) -> list[str]:
    """Check migration map for common mistakes."""
    errors = []
    all_catalog_codes = set()
    for mod in catalog['modules'].values():
        all_catalog_codes.update(mod.keys())

    for old_code in mmap.get('renamed', {}):
        if old_code not in all_catalog_codes:
            errors.append(f"renamed key '{old_code}' not in current catalog (typo or already removed?)")
    for new_code in mmap.get('renamed', {}).values():
        if new_code not in all_catalog_codes:
            errors.append(f"renamed target '{new_code}' not in current catalog (typo?)")
    for code in mmap.get('removed', []):
        if code not in all_catalog_codes:
            errors.append(f"removed '{code}' not in current catalog")
    return errors


def migrate(migration_map_path: str, dry_run: bool = True):
    mmap = load_json(migration_map_path)
    catalog = load_json(CATALOG_PATH)
    faq = load_json(FAQ_PATH)
    evals = load_json(EVAL_PATH)

    # Validate first
    errors = validate_migration_map(mmap, catalog)
    if errors:
        print("=== Migration map validation errors ===")
        for e in errors:
            print(f"  ERROR: {e}")
        if dry_run:
            print("Fix these before running with --write.")
        return

    # FAQ
    faq_stats, affected_ids = migrate_faq(faq, mmap, catalog)
    # Eval
    eval_stats = migrate_eval(evals, mmap)
    # Update catalog with added indicators
    for added in mmap.get('added', []):
        # Place in "工作情况" by default; user can move later
        if '工作情况' in catalog['modules']:
            catalog['modules']['工作情况'][added['code']] = {
                'description': added.get('description', ''),
                'type': added.get('type', '单选'),
                '_new_in_version': mmap.get('target_version', 'unknown'),
            }
    # Remove from catalog
    for code in mmap.get('removed', []):
        for mod_name, indicators in catalog['modules'].items():
            if code in indicators:
                del indicators[code]
                break
    # Rename in catalog
    for old_code, new_code in mmap.get('renamed', {}).items():
        for mod_name, indicators in catalog['modules'].items():
            if old_code in indicators:
                indicators[new_code] = indicators.pop(old_code)
                break
    catalog['last_updated'] = mmap.get('date', catalog.get('last_updated', ''))
    catalog['version'] = mmap.get('target_version', catalog['version'])
    catalog['total_indicators'] = sum(len(mod) for mod in catalog['modules'].values())

    # Report
    print(f"=== Migration Report ===")
    print(f"Renamed entries:  {faq_stats['renamed_entries']}")
    print(f"Removed flags:    {faq_stats['removed_flags']}")
    print(f"Body text fixes:  {faq_stats['body_replaces']}")
    print(f"Eval text fixes:  {eval_stats['eval_body_replaces']}")
    print()

    if faq_stats['errors']:
        print("Validation errors:")
        for e in faq_stats['errors']:
            print(f"  {e}")
        print()

    for change_type, ids in sorted(affected_ids.items()):
        print(f"{change_type}: {len(ids)} entries ({', '.join(ids[:10])}{'...' if len(ids) > 10 else ''})")

    if faq_stats['removed_flags'] > 0:
        print(f"\n{faq_stats['removed_flags']} entries tagged with _indicators_removed — open faq.json and search for this key to review them.")

    if dry_run:
        print("\n[Dry run — no changes written. Use --write to apply.]")
    else:
        with open(FAQ_PATH, 'w', encoding='utf-8') as f:
            json.dump(faq, f, ensure_ascii=False, indent=2)
        with open(EVAL_PATH, 'w', encoding='utf-8') as f:
            json.dump(evals, f, ensure_ascii=False, indent=2)
        with open(CATALOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)
        print(f"\nWritten: {FAQ_PATH}, {EVAL_PATH}, {CATALOG_PATH}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_indicators.py <migration_map.json> [--write]")
        sys.exit(1)
    migration_map_path = sys.argv[1]
    dry_run = '--write' not in sys.argv
    migrate(migration_map_path, dry_run=dry_run)
