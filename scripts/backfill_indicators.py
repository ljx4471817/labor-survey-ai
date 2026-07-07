"""
Backfill indicators field into all FAQ entries.

Reads faq.json, extracts indicator codes (F*/H*) from source, question,
and answer fields, writes an `indicators` array on each entry.

Priority: source > question > answer
Entries where no indicator can be inferred are tagged with `_indicators_review: true`.

Usage:
  python scripts/backfill_indicators.py                 # dry-run (report only)
  python scripts/backfill_indicators.py --write          # apply changes
  python scripts/backfill_indicators.py --write --force  # overwrite existing indicators
"""

import json
import re
import sys
from pathlib import Path
from collections import OrderedDict

ROOT = Path(__file__).resolve().parent.parent
FAQ_PATH = ROOT / "knowledge-base" / "qa" / "faq.json"
CATALOG_PATH = ROOT / "knowledge-base" / "indicator_catalog.json"

# Match F10, F10.1, H1, H2 etc — allows Chinese chars after (no \b on right)
INDICATOR_RE = re.compile(r'(?<![A-Za-z])[FH]\d+(?:\.\d+)?')
# Patterns that mention indicators without the prefix (e.g. "问题10" → F10)
PROBLEM_RE = re.compile(r'问题\s*(\d+(?:\.\d+)?)')
# Source contains indicator keyword
SOURCE_IND_RE = re.compile(r'[FH](\d+(?:\.\d+)?)')


def load_catalog():
    with open(CATALOG_PATH, encoding='utf-8') as f:
        return json.load(f)


def extract_from_source(source: str) -> set[str]:
    """Extract indicators from source field. High confidence."""
    codes = set()
    # Direct F/H references (e.g. "F10", "F10/F11", "H2/H3")
    codes.update(INDICATOR_RE.findall(source))
    # "问题10" → F10, "问题10/11" → F10 + F11, "问题10、11、12" → F10+F11+F12
    for m in re.finditer(r'问题\s*([\d./、]+)', source):
        nums = re.split(r'[/、]', m.group(1))
        for num in nums:
            num = num.strip()
            if num:
                codes.add(f'F{num}')
    return codes


def extract_from_text(text: str) -> set[str]:
    """Extract indicators from question/answer text."""
    codes = set()
    codes.update(INDICATOR_RE.findall(text))
    # "F10-F12" range pattern
    for m in re.finditer(r'[FH](\d+)\s*[-–—]\s*[FH](\d+)', text):
        prefix = m.group(0)[0]  # 'F' or 'H'
        start = int(m.group(1))
        end = int(m.group(2))
        for i in range(start, end + 1):
            codes.add(f'{prefix}{i}')
    return codes


def validate_against_catalog(codes: set[str], catalog: dict) -> tuple[set[str], set[str]]:
    """Split codes into valid (in catalog) and unknown."""
    all_valid = set()
    for module_indicators in catalog['modules'].values():
        all_valid.update(module_indicators.keys())
    valid = codes & all_valid
    unknown = codes - all_valid
    return valid, unknown


def backfill(dry_run: bool = True, force: bool = False):
    catalog = load_catalog()
    all_catalog_codes = set()
    for mod in catalog['modules'].values():
        all_catalog_codes.update(mod.keys())

    with open(FAQ_PATH, encoding='utf-8') as f:
        faq = json.load(f)

    stats = {
        'total': len(faq),
        'already_had': 0,
        'updated': 0,
        'review_needed': 0,
        'unknown_codes': set(),
        'by_indicator': {},
    }
    review_items = []

    for entry in faq:
        # Skip if already has indicators and not forcing
        if 'indicators' in entry and entry['indicators'] and not force:
            stats['already_had'] += 1
            continue

        source = entry.get('source', '')
        question = entry.get('question', '')
        answer = entry.get('answer', '')

        codes = set()
        # Priority 1: source field
        codes.update(extract_from_source(source))
        # Priority 2: question text
        codes.update(extract_from_text(question))
        # Priority 3: answer text (only if still empty)
        if not codes:
            codes.update(extract_from_text(answer))

        # Validate
        valid, unknown = validate_against_catalog(codes, catalog)
        stats['unknown_codes'].update(unknown)

        if valid:
            entry['indicators'] = sorted(valid)
            stats['updated'] += 1
            for c in valid:
                stats['by_indicator'][c] = stats['by_indicator'].get(c, 0) + 1
        else:
            entry['indicators'] = []
            entry['_indicators_review'] = True
            stats['review_needed'] += 1
            review_items.append({
                'id': entry['id'],
                'question': question[:60],
                'source': source[:80],
                'guessed': sorted(codes) if codes else [],
            })

    # Report
    print(f"=== Backfill Report ===")
    print(f"Total entries:      {stats['total']}")
    print(f"Already had field:  {stats['already_had']}")
    print(f"Updated:            {stats['updated']}")
    print(f"Needs manual review:{stats['review_needed']}")
    print()

    if stats['unknown_codes']:
        print(f"Unknown codes found (not in catalog): {sorted(stats['unknown_codes'])}")
        print()

    # Top indicators
    print("Top 10 indicators by entry count:")
    sorted_inds = sorted(stats['by_indicator'].items(), key=lambda x: -x[1])
    for code, count in sorted_inds[:10]:
        desc = "?"
        for mod_name, indicators in catalog['modules'].items():
            if code in indicators:
                desc = indicators[code]['description']
                break
        print(f"  {code}: {count} entries — {desc}")

    if review_items:
        print(f"\n=== Entries needing manual review ({len(review_items)}) ===")
        for item in review_items:
            print(f"  [{item['id']}] {item['question']}")
            print(f"       source: {item['source']}")
            if item['guessed']:
                print(f"       guessed: {', '.join(item['guessed'])} (not in catalog)")
            else:
                print(f"       no indicator pattern found")

    if dry_run:
        print("\n[Dry run — no changes written. Use --write to apply.]")
    else:
        with open(FAQ_PATH, 'w', encoding='utf-8') as f:
            json.dump(faq, f, ensure_ascii=False, indent=2)
        print(f"\nWritten {FAQ_PATH}")

    return stats


if __name__ == '__main__':
    dry_run = '--write' not in sys.argv
    force = '--force' in sys.argv
    backfill(dry_run=dry_run, force=force)
