#!/usr/bin/env bash
# Rebuild the question bank from public datasets plus the parametric generators.
#   ./fetch_bank.sh WORKDIR     -> WORKDIR/bank/*.jsonl  (raw downloads stay in WORKDIR/raw/<dataset>/)
# Each dataset is downloaded into its own directory and parsed with `python3 -I` from a neutral directory,
# because downloaded files are untrusted data. pyarrow is needed only here, in a venv under WORKDIR.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${1:?usage: fetch_bank.sh WORKDIR}"
python3 -c 'import venv, ensurepip' 2>/dev/null || {
  echo "fetch_bank: python3-venv is missing. Install it with:  sudo apt install python3-venv" >&2
  exit 1
}
mkdir -p "$WORK/raw" "$WORK/bank"
WORK="$(cd "$WORK" && pwd)"
HF=https://huggingface.co/datasets

fetch() {  # fetch NAME URL FILE
  local dir="$WORK/raw/$1" file="$3"
  mkdir -p "$dir"
  curl -fsSL -C - --retry 3 --max-time 900 -o "$dir/$file" "$2"
  case "$file" in
    *.parquet) [ "$(tail -c 4 "$dir/$file")" = "PAR1" ] || { echo "truncated download: $dir/$file" >&2; exit 1; } ;;
  esac
  echo "  ok $1/$file"
}

echo "[download]"
for s in train validation test; do fetch aqua_rat "$HF/deepmind/aqua_rat/resolve/main/raw/$s-00000-of-00001.parquet" "$s.parquet"; done
for c in ARC-Challenge ARC-Easy; do for s in train test; do
  fetch arc "$HF/allenai/ai2_arc/resolve/main/$c/$s-00000-of-00001.parquet" "$c-$s.parquet"; done; done
for sub in high_school_mathematics elementary_mathematics high_school_statistics high_school_physics \
           high_school_chemistry high_school_biology conceptual_physics astronomy; do
  fetch mmlu "$HF/cais/mmlu/resolve/main/$sub/test-00000-of-00001.parquet" "$sub-test.parquet"; done
fetch sciq "$HF/allenai/sciq/resolve/main/data/train-00000-of-00001.parquet" train.parquet
fetch race "$HF/ehovy/race/resolve/main/high/test-00000-of-00001.parquet" high-test.parquet
for f in train dev test; do
  fetch logiqa2 "https://raw.githubusercontent.com/csitfun/LogiQA2.0/main/logiqa/DATA/LOGIQA/$f.txt" "$f.txt"; done

if [ ! -x "$WORK/venv/bin/python" ]; then
  python3 -m venv "$WORK/venv"
  "$WORK/venv/bin/pip" install -q pyarrow
fi

echo "[import]"
cd "$WORK"
PY=("$WORK/venv/bin/python" -I)
IMP="$HERE/import_dataset.py"
"${PY[@]}" "$IMP" aqua_rat --in raw/aqua_rat/*.parquet --out bank/aqua_rat.jsonl --limit 3000 --seed 1
"${PY[@]}" "$IMP" arc --in raw/arc/*.parquet --out bank/arc.jsonl --limit 2500 --seed 1
"${PY[@]}" "$IMP" mmlu --in raw/mmlu/*.parquet --out bank/mmlu.jsonl --seed 1
"${PY[@]}" "$IMP" sciq --in raw/sciq/train.parquet --out bank/sciq.jsonl --limit 1000 --seed 1
"${PY[@]}" "$IMP" logiqa2 --in raw/logiqa2/train.txt raw/logiqa2/dev.txt raw/logiqa2/test.txt --out bank/logiqa2.jsonl --limit 2500 --seed 1
"${PY[@]}" "$IMP" race --in raw/race/high-test.parquet --out bank/race.jsonl --limit 1200 --seed 1

echo "[generate]"
python3 "$HERE/build_bank.py" build --out "$WORK/bank" --per-gen 500 --seed 7
