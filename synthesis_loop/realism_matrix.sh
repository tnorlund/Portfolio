#!/usr/bin/env bash
# Build the realism render matrix: cleaned bundle + receipt_dir + one hybrid per operation, all 8 merchants.
set -uo pipefail
cd ~/Portfolio_content
export PYTHONPATH="$PWD/receipt_dynamo:$PWD/receipt_agent:$PWD/receipt_upload"
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 PORTFOLIO_ENV=dev
PY=$HOME/.synth-venv/bin/python
EXPORTS=$HOME/synth-batch/exports
OUT=/tmp/realism
rm -rf "$OUT"; mkdir -p "$OUT"

split_dir() {  # $1 export -> $2 dir
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
  m=$(basename "$f" .json)
  mn=$("$PY" -c "import json;print(json.load(open('$f')).get('merchant_name',''))" 2>/dev/null)
  [ -z "$mn" ] && continue
  D="$OUT/$m"; mkdir -p "$D"
  if [ -f "/tmp/clean/$m/bundle.json" ]; then
    cp "/tmp/clean/$m/bundle.json" "$D/bundle.json"
  else
    "$PY" scripts/verify_synthetic_replay.py local-pipeline --receipt-file "$f" \
      --artifact-output-dir "$D/artifacts" --bundle-output "$D/bundle.json" --max-candidates 40 >"$D/gen.log" 2>&1
  fi
  split_dir "$f" "$D/receipt_dir"
  "$PY" synthesis_loop/render_matrix.py "$D/bundle.json" "$D/receipt_dir" "$mn" "$D" >>"$D/gen.log" 2>&1
  n=$(ls "$D"/*.hybrid.png 2>/dev/null | wc -l | tr -d ' ')
  echo "$m ($mn): $n operation renders"
done
echo "MATRIX DONE -> $OUT  (total renders: $(ls "$OUT"/*/*.hybrid.png 2>/dev/null | wc -l | tr -d ' '))"
