"""Build the internal THOR role index; output must be a new directory."""
import argparse
import json

from lastmile_dataflow.scenes.loading.asset_profiles import build_asset_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--categories', nargs='+', default=[])
    parser.add_argument('--max-per-category', type=int)
    args = parser.parse_args()
    if args.max_per_category is not None and args.max_per_category < 1:
        parser.error('--max-per-category must be positive')
    result = build_asset_index(args.source_root, args.output,
        categories=args.categories, max_per_category=args.max_per_category)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
