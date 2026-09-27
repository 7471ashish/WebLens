/**
 * WebLens Cross-Browser Extension Builder & Packager
 * Generates unpacked directories and production zip archives for:
 *   - Google Chrome (dist/chrome/ + dist/weblens-chrome-v1.0.0.zip)
 *   - Mozilla Firefox (dist/firefox/ + dist/weblens-firefox-v1.0.0.zip)
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT_DIR = path.resolve(__dirname, '..');
const EXT_DIR = path.join(ROOT_DIR, 'extension');
const DIST_DIR = path.join(ROOT_DIR, 'dist');
const CHROME_DIST = path.join(DIST_DIR, 'chrome');
const FIREFOX_DIST = path.join(DIST_DIR, 'firefox');

const SHARED_FILES = [
  'side_panel.html',
  'side_panel.css',
  'side_panel.js',
  'popup.html',
  'popup.js',
  'background.js',
];

const SHARED_DIRS = ['icons'];

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  const entries = fs.readdirSync(src, { withFileTypes: true });
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function buildTarget(targetName, targetDir, manifestSource) {
  console.log(`[WebLens Build] Building ${targetName}...`);
  fs.rmSync(targetDir, { recursive: true, force: true });
  fs.mkdirSync(targetDir, { recursive: true });

  // Copy shared files
  for (const f of SHARED_FILES) {
    const src = path.join(EXT_DIR, f);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(targetDir, f));
    } else {
      console.warn(`[Warning] Missing shared file: ${f}`);
    }
  }

  // Copy shared directories
  for (const d of SHARED_DIRS) {
    const src = path.join(EXT_DIR, d);
    if (fs.existsSync(src)) {
      copyDirRecursive(src, path.join(targetDir, d));
    }
  }

  // Copy target manifest
  const manifestDest = path.join(targetDir, 'manifest.json');
  fs.copyFileSync(path.join(EXT_DIR, manifestSource), manifestDest);

  console.log(`[WebLens Build] -> Created ${targetDir}`);
}

function createZip(sourceDir, zipName) {
  const zipPath = path.join(DIST_DIR, zipName);
  if (fs.existsSync(zipPath)) fs.unlinkSync(zipPath);

  const pythonCmd = fs.existsSync(path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'))
    ? path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe')
    : 'python';

  const pyScript = `
import sys, os, zipfile
source_dir = sys.argv[1]
zip_path = sys.argv[2]
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(source_dir):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, source_dir)
            zipf.write(full, rel)
print(f"Packed: {zip_path}")
`;

  try {
    const { execFileSync } = require('child_process');
    execFileSync(pythonCmd, ['-c', pyScript, sourceDir, zipPath], { cwd: ROOT_DIR });
    console.log(`[WebLens Build] -> Generated zip: ${zipName}`);
  } catch (err) {
    console.error(`[Error] Failed to create zip for ${sourceDir}:`, err.message);
  }
}

function main() {
  fs.mkdirSync(DIST_DIR, { recursive: true });

  buildTarget('Chrome (MV3)', CHROME_DIST, 'manifest.chrome.json');
  buildTarget('Firefox (MV3 / Gecko)', FIREFOX_DIST, 'manifest.firefox.json');

  createZip(CHROME_DIST, 'weblens-chrome-v1.0.0.zip');
  createZip(FIREFOX_DIST, 'weblens-firefox-v1.0.0.zip');

  console.log('\n[WebLens Build] Build complete!');
  console.log('Unpacked directories:');
  console.log(`  - Chrome:  dist/chrome/ (Load in chrome://extensions)`);
  console.log(`  - Firefox: dist/firefox/ (Load in about:debugging#/runtime/this-firefox)`);
  console.log('Store Packages:');
  console.log(`  - Chrome Web Store: dist/weblens-chrome-v1.0.0.zip`);
  console.log(`  - Mozilla AMO:      dist/weblens-firefox-v1.0.0.zip\n`);
}

main();
