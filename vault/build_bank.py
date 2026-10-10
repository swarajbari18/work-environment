#!/usr/bin/env python3
"""Build or inspect a question bank directory.

  build_bank.py build --out DIR [--per-gen 600] [--seed N]   write generated.jsonl (parametric questions)
  build_bank.py stats DIR                                    count questions by source and category, list problems

Imported datasets live beside it as their own *.jsonl files (see import_dataset.py).
"""
import argparse
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generators  # noqa: E402
import questions  # noqa: E402


def cmd_build(args):
    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    qs = generators.build(rng, args.per_gen)
    path = os.path.join(args.out, "generated.jsonl")
    questions.write_jsonl(path, qs)
    print(f"wrote {len(qs)} questions to {path}")
    return cmd_stats(argparse.Namespace(dir=args.out))


def cmd_stats(args):
    bank, problems = questions.load_bank(args.dir)
    print(f"{len(bank)} usable questions in {args.dir}")
    by_cat = Counter(q["category"] for q in bank.values())
    by_src = Counter(q["source"].split(":")[0] if q["source"].startswith("gen:") else q["source"] for q in bank.values())
    for cat in questions.CATEGORIES:
        print(f"  {cat:<12}{by_cat.get(cat, 0):>7}")
    print("by source:", dict(by_src))
    for p in problems[:20]:
        print("PROBLEM", p)
    if len(problems) > 20:
        print(f"... and {len(problems) - 20} more problems")
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--per-gen", type=int, default=600)
    b.add_argument("--seed", type=int, default=None)
    s = sub.add_parser("stats")
    s.add_argument("dir")
    args = parser.parse_args(argv)
    return {"build": cmd_build, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
