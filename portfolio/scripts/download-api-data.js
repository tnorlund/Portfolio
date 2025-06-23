import fs from 'fs';
import path from 'path';

async function main() {
  const apiUrl = 'https://api.tylernorlund.com/receipts?limit=1';
  const res = await fetch(apiUrl, { headers: { 'Content-Type': 'application/json' } });
  if (!res.ok) {
    throw new Error(`Failed to fetch: ${res.status} ${res.statusText}`);
  }
  const data = await res.json();
  const outDir = path.join('portfolio', 'tests', 'fixtures');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'receipts.json'), JSON.stringify(data, null, 2));
  console.log('Saved to', path.join(outDir, 'receipts.json'));
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
