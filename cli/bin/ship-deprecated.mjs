#!/usr/bin/env node
process.stderr.write(
  "warning: 'ship' is deprecated; use 'shipctl' (will be removed in @elmundi/ship-cli@0.5). See cli/README.md#deprecated-alias.\n",
);
await import("./shipctl.mjs");
