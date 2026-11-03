"""Run all application milestones without changing their established discovery roots."""

import argparse
from pathlib import Path
import unittest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=['all','p0','p1','p2'],default='all')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    suite=unittest.TestSuite()
    for name in (['app','p1','p2'] if args.suite=='all' else ['app' if args.suite=='p0' else args.suite]):
        # Each existing discovery root keeps its original helper imports intact.
        suite.addTests(unittest.TestLoader().discover(str(root/'tests'/name),pattern='test_*.py'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
