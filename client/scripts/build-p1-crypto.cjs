const { buildSync } = require('esbuild');
const path = require('node:path');

buildSync({
  entryPoints: [path.join(__dirname, '..', 'core', 'discovery', 'p1-crypto-entry.js')],
  outfile: path.join(__dirname, '..', 'core', 'discovery', 'p1-crypto-bundle.js'),
  bundle: true,
  platform: 'browser',
  format: 'cjs',
  target: 'es2020',
  minify: true,
  legalComments: 'none',
});
