#!/usr/bin/env node
'use strict';

const { spawnSync } = require('node:child_process');
const { version, optionalDependencies } = require('../package.json');
const name = `snek-lang-${process.platform}-${process.arch}`;

if (!Object.hasOwn(optionalDependencies, name)) {
  console.error(`snek: unsupported platform ${process.platform}/${process.arch}`);
  process.exit(2);
}

let binary;
try {
  const metadata = require(`${name}/package.json`);
  if (metadata.version !== version) throw new Error('platform package version mismatch');
  binary = require.resolve(`${name}/bin/snek${process.platform === 'win32' ? '.exe' : ''}`);
} catch (error) {
  console.error(`snek: cannot load ${name}@${version}: ${error.message}`);
  console.error('Reinstall snek-lang with optional dependencies enabled. No runtime downloads are performed.');
  process.exit(2);
}

const result = spawnSync(binary, process.argv.slice(2), { stdio: 'inherit' });
if (result.error) {
  console.error(`snek: ${result.error.message}`);
  process.exit(2);
}
if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status ?? 2);
}
