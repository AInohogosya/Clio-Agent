import { strict as assert } from 'node:assert';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { readStamp } from '../dist/config-file.js';
import { emptyConfig, saveConfig, watchConfig } from '../dist/store.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function withTempConfig(run) {
  const directory = mkdtempSync(join(tmpdir(), 'phone-watch-'));
  const previous = process.env.PROJECT_PHONE_CONFIG;
  process.env.PROJECT_PHONE_CONFIG = join(directory, 'config.json');
  const restore = () => {
    if (previous === undefined) delete process.env.PROJECT_PHONE_CONFIG;
    else process.env.PROJECT_PHONE_CONFIG = previous;
    rmSync(directory, { recursive: true, force: true });
  };
  const result = run({ directory, path: join(directory, 'config.json') });
  if (result && typeof result.then === 'function') return result.finally(restore);
  restore();
  return result;
}

test('an unreadable stamp is "not now", never "changed" and never a throw', async () => {
  await withTempConfig(async ({ directory, path }) => {
    saveConfig(emptyConfig(), { origin: 'cli' });
    assert.match(String(readStamp(path)), /^\d[\d.]*:\d+:\d+$/, 'a readable file has a stamp');

    // The parent stops being a directory: every stat now fails with ENOTDIR
    // rather than reporting ENOENT, which is what an absent file looks like.
    rmSync(directory, { recursive: true, force: true });
    writeFileSync(directory, 'no longer a directory');
    assert.equal(readStamp(path), null);

    // A file that is genuinely gone is still simply absent, so a watcher can
    // tell "deleted" from "cannot tell" and act differently on each.
    rmSync(directory, { force: true });
    assert.equal(readStamp(path), 'absent');
  });
});

test('the watcher keeps working after the path was unreadable for a while', async () => {
  await withTempConfig(async ({ directory }) => {
    saveConfig(emptyConfig(), { origin: 'cli' });
    const seen = [];
    const stop = watchConfig((config) => seen.push(config.revision));
    try {
      rmSync(directory, { recursive: true, force: true });
      writeFileSync(directory, 'in the way');
      await sleep(150);
      // Put the directory back and write for real: the watch has to come back
      // rather than stay deaf, and must not report a state nobody wrote.
      rmSync(directory, { force: true });
      const saved = saveConfig(emptyConfig(), { origin: 'cli' });
      await sleep(300);
      assert.ok(seen.includes(saved.revision), 'the later write is reported');
    } finally {
      stop();
    }
  });
});
