#!/bin/zsh
# Build logo masters for all merchants, one at a time, each logged separately.
W=~/Portfolio_grid_discipline
export PYTHONPATH="$W/receipt_agent:$W/receipt_dynamo:$W/receipt_upload"
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 PORTFOLIO_ENV=dev
PY=~/Portfolio/.venv/bin/python
cd "$W"
run() {
  local name="$1" dir="$2"
  local out="/tmp/gridfix/$dir/logo"
  mkdir -p "$out"
  echo "=== $name -> $out ===" > "$out/run.log"
  "$PY" synthesis_loop/logo_master.py "$name" "$out" >> "$out/run.log" 2>&1
  echo "[done $name]"
}
run "Vons" vons
run "Smith's" smiths
run "Sprouts Farmers Market" sprouts_farmers_market
run "Amazon Fresh" amazon_fresh
run "Target" target
run "Gelson's Westlake Village" gelsons_westlake_village
echo "ALL LOGO MASTERS DONE"
