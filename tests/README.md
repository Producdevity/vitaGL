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


# Shader first-use diagnostics

```sh
python3 tests/shader_diagnostics.py
python3 tests/shader_diagnostics.py --sanitizer thread
```

This extracts the actual custom compile/cache/link functions, FFP dirty-stage
cache/compiler branches, vertex patch cache and fragment patch helper. It tests
cache-enabled and cache-disabled builds, with normal error handling,
`SKIP_ERROR_HANDLING`, and `LOG_ERRORS`. The default run uses ASan/UBSan.
`--source-root` selects another checkout; `CC` selects a native compiler.

Covered cases include immediate hits/misses, postponed compile requests,
postponed vertex/fragment hit combinations and precompiled-stage skipping,
compiler null results, registration failures, missing-shader/relink exits,
completion after reflection, FFP dirty/non-dirty paths, vertex table/MRU hits,
eviction, patch failure/retry, ABI rejection, reset, and owner-thread snapshots
published through a pthread join. Disabled cases require zero clock reads and
unchanged counters. Mocked I/O, compiler, registration and allocation counts
are compared with diagnostics enabled for the immediate, postponed and FFP
success paths.

The reflection fixture has zero parameters; shader/compiler/GXM and filesystem
operations are mocked. FFP compiler failure still reaches its pre-existing
unchecked copy; the fixture intercepts that copy after checking the counter.
These tests do not claim to fix failure handling, validate cache data, verify
rendered pixels, or reproduce driver compilation latency.

## API and counter meaning

`vglSetShaderDiagnostics`, `vglResetShaderDiagnostics`, and
`vglGetShaderDiagnostics` must run on the same GL owner, between GL calls.
They do not synchronize threads. Enable defaults to false; reset preserves the
setting. Pass `sizeof(vglShaderDiagnostics)` and
`VGL_SHADER_DIAGNOSTICS_ABI_VERSION` to get a copy. A size/version mismatch or
null destination returns false without writing output or changing GL errors.
An enabled snapshot reads `sceKernelGetProcessTimeWide`; a disabled snapshot has
`snapshot_us=0`. `available_stages` is a bitmask indexed by stage; the custom
cache bits are absent without `HAVE_SHADER_CACHE`. Counters are cumulative since
reset. The first/last frames are `vgl_framecount` at request time and only valid
when requests is nonzero. Duration maxima are cumulative, not subtractable
window maxima; reset before a window when a window maximum is needed.

- Cache positive/negative results mean open hit/miss. Time covers the existing
  lookup plus loading on a hit (including custom deserialization/registration),
  or the lookup alone on a miss. It does not prove a valid read or shader.
- Compile results mean the existing vitaShaRK call returned non-null/null.
  Timing covers that call only, excluding translation, serialization and
  registration. Custom registration also covers binary-loading APIs.
- Link requests include missing/relinked returns. Positive results count only
  reaching the function end; they do not assert driver success. Link time is
  inclusive of cache, translation and compiler work and must not be added to
  child times. Existing missing/relink checks and error behavior remain intact.
- Registration and patch results mean GXM returned zero/nonzero; these stages
  never read a timer. A patch API request may hit GXM's own cache. Vertex
  table/MRU hits do not touch diagnostics at all.

The POD contains no shader source identifiers or event history. Cache writes,
translation, built-in precompiled startup registration and host Vulkan pipeline
creation are not separately counted/timed. No cache policy, shader flags,
rendering decision, log emission or allocation is added by diagnostics. The
feature remains opt-in and requires paired header/library integration before
runtime validation; these host tests make no performance claim.
