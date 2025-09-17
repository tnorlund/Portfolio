# receipt_layoutlm

Install (from repo root):

```bash
pip install -e receipt_dynamo
pip install -e receipt_layoutlm
```

If PyTorch wheels fail on Python 3.13, use Python 3.12:

```bash
pyenv local 3.12.5  # or create a 3.12 venv
python -m venv .venv && source .venv/bin/activate
pip install -e receipt_dynamo
pip install -e receipt_layoutlm
```

Train:

```bash
export DYNAMO_TABLE_NAME=<your-table>
layoutlm-cli train --job-name test
```
