import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export function loadState(path) {
  if (!existsSync(path)) return { offsets: {} };
  try {
    const raw = readFileSync(path, 'utf8');
    const data = JSON.parse(raw);
    return { offsets: data.offsets ?? {} };
  } catch {
    return { offsets: {} };
  }
}

export function saveState(path, state) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2), 'utf8');
}
