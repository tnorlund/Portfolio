#!/usr/bin/env bash
# synthesis_loop/regen_merchants.sh — regenerate + render synthetic candidates for ALL merchants
# via bundle mode (the path that runs the real synthesis code). Writes per-merchant bundles +
# composites under $OUT (default /tmp/mm). Used by run_loop_mm.sh each round so EVERY merchant is
# covered and scored. Exports needed: PYTHONPATH/DYNAMODB set by the caller (or run standalone).
set -uo pipefail
REPO="${REPO:-$HOME/Portfolio_synth_loop}"
OUT="${OUT:-/tmp/mm}"
EXPORTS="${EXPORTS:-$HOME/synth-batch/exports}"
PY="${PYTHON_BIN:-$([ -x "$HOME/.synth-venv/bin/python" ] && echo "$HOME/.synth-venv/bin/python" || echo "$HOME/.coreml-venv/bin/python")}"
LIMIT="${RENDER_LIMIT:-2}"
MAXCAND="${MAX_CANDIDATES:-40}"
cd "$REPO"
export PYTHONPATH="$REPO/receipt_dynamo:$REPO/receipt_agent:$REPO/receipt_upload${PYTHONPATH:+:$PYTHONPATH}"
export DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}" PORTFOLIO_ENV="${PORTFOLIO_ENV:-dev}"
rm -rf "$OUT"; mkdir -p "$OUT/composites"

_split_export() {  # $1 export file -> $2 per-image dir
  "$PY" - "$1" "$2" <<'PY'
import json,os,sys,collections
exp=json.load(open(sys.argv[1])); outdir=sys.argv[2]; os.makedirs(outdir,exist_ok=True)
keys=[k for k in ("receipts","receipt_lines","receipt_words","receipt_word_labels","receipt_places") if k in exp]
by=collections.defaultdict(lambda:{k:[] for k in keys})
for k in keys:
  for it in exp.get(k,[]):
    iid=it.get("image_id") or (it.get("receipt") or {}).get("image_id")
    if iid: by[iid][k].append(it)
mn=exp.get("merchant_name","")
for iid,d in by.items():
  d["merchant_name"]=mn; json.dump(d,open(os.path.join(outdir,f"{iid}.json"),"w"))
PY
}

for f in "$EXPORTS"/*.json; do
  slug=$(basename "$f" .json)
  mn=$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('merchant_name',''))" "$f" 2>/dev/null)
  [ -z "$mn" ] && continue
  D="$OUT/$slug"; mkdir -p "$D"
  "$PY" scripts/verify_synthetic_replay.py local-pipeline --receipt-file "$f" \
    --artifact-output-dir "$D/artifacts" --bundle-output "$D/bundle.json" --max-candidates "$MAXCAND" >"$D/gen.log" 2>&1
  ex=$("$PY" -c "import json;print(len(json.load(open('$D/bundle.json')).get('synthetic_training_examples',[])))" 2>/dev/null || echo 0)
  echo "$slug ($mn): $ex examples"
  [ "${ex:-0}" -eq 0 ] && continue
  _split_export "$f" "$D/receipt_dir"
  "$PY" scripts/render_synthetic_receipts.py --bundle "$D/bundle.json" --receipt-dir "$D/receipt_dir" \
    --merchant "$mn" --out-dir "$D/render" --limit "$LIMIT" >>"$D/gen.log" 2>&1
  i=0
  for png in "$D"/render/*.real_vs_synthetic.png; do
    [ -f "$png" ] || continue; i=$((i+1)); [ "$i" -gt "$LIMIT" ] && break
    cp "$png" "$OUT/composites/${slug}-$i.png"
  done
done
echo "regen done -> $OUT (composites: $(ls "$OUT/composites"/*.png 2>/dev/null | wc -l | tr -d ' '))"
