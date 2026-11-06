# Compressed upload regression tests

Run from the repository root with Python 3 and a native C compiler supporting
AddressSanitizer and UndefinedBehaviorSanitizer:

```sh
python3 tests/compressed_uploads.py
```

Set `CC` to select another compiler. Pass a source file to test a previous
revision without modifying the checkout:

```sh
git show e119b3ec158067725659a25b650ebb8f537d1e8f:source/utils/gpu_utils.c > /tmp/vitagl-upstream-gpu_utils.c
python3 tests/compressed_uploads.py /tmp/vitagl-upstream-gpu_utils.c
```

The harness extracts the actual compressed upload and mip-size functions. It
mocks the GXM transfer queue, allocators, texture descriptors, and swizzlers. It
tests both staged uploads and `TEXTURE_UPLOADS_SPEEDHACK` direct uploads:

- Sub-block and thin mip levels must reach the CPU swizzler without a
  zero-sized GPU transfer.
- Mip reallocation, release, and upload return must occur after queued
  transfers finish.
- Full blocks must retain the GPU transfer path.

The upstream revision above fails the first two cases in both upload modes.
The fixed source passes all six cases under both sanitizers. The tests check
control flow and transfer lifetime; they do not validate GPU pixels, swizzler
output, driver behavior, or device performance. Emulator and hardware testing
remain necessary.

# Clear uniform-reservation regression tests

```sh
python3 tests/clear_uniforms.py
```

The harness extracts `glClear` from `source/misc.c` and mocks GXM calls and
state-restoration helpers. It runs with and without `SKIP_ERROR_HANDLING`,
under AddressSanitizer and UndefinedBehaviorSanitizer. Each mode covers:

- Vertex and fragment reservation failures, including untouched output pointers.
- Allocation failures, invalid-scene errors and unexpected null buffers.
- No uniform writes or clear draws after either reservation fails.
- Preservation of a pending GL error and restoration of viewport, culling,
  depth, polygon, stencil, fragment-enable and cached uniform-buffer state.
- Successful color/depth/stencil combinations, depth masks, disabled depth
  testing and floating-point framebuffers, with both reservations completed
  before uniform writes.

Each of the 20 cases checks 56 state combinations. Test an older source without
changing the checkout:

```sh
git show e119b3ec158067725659a25b650ebb8f537d1e8f:source/misc.c > /tmp/vitagl-upstream-misc.c
python3 tests/clear_uniforms.py /tmp/vitagl-upstream-misc.c
```

That source reaches uniform writes after injected reservation failures and
violates the reserve-before-write ordering check. The fixed source passes all
cases. These tests validate the actual `glClear` control flow against mocks;
they do not reproduce device allocation exhaustion or verify rendered pixels.
`scene_reset()` still has a separate, pre-existing limitation: it returns no
status when `sceGxmBeginScene` fails. This change does not add scene recovery or
retry failed reservations.
