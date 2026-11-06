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
