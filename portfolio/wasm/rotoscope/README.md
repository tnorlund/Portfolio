# Rotoscope WebAssembly kernel

This directory contains the allocation-free scalar WebAssembly implementation
of the homepage rotoscope. The TypeScript implementation under
`components/home/Rotoscope/algorithm.ts` remains the readable correctness
oracle.

The JavaScript host owns one reusable arena above AssemblyScript's static data.
It calls `requiredBytes`, then `ensureCapacity`, copies RGBA bytes to
`inputRgbaPtr` and one focus-tier byte per pixel to `focusTierPtr`, and finally
calls `run`. Focus bytes are `0` for face, `1` for body, and `2` for background.
The output overwrites the arena's input RGBA buffer only after region palette
sums have been accumulated.

The ABI version is `1`. `run` and `status` return `0` on success, `1` for
invalid dimensions, `2` when `ensureCapacity` was not called with enough
bytes, `3` for a focus byte outside `0...2`, and `4` for an invalid/overflowing
layout. Dimensions are capped at 2048 per side and 1,048,576 total pixels.
The arena requires approximately 23 bytes per pixel plus 20 bytes per requested
marker and a fixed 2 KiB watershed bucket table.

`ensureCapacity` is the only export that grows linear memory. Any host view of
`memory.buffer` must therefore be reacquired after that call.

Build from `portfolio/` with Node 22:

```sh
PATH=/opt/homebrew/opt/node@22/bin:$PATH \
  ./node_modules/.bin/asc --config wasm/rotoscope/asconfig.json --target release
```
