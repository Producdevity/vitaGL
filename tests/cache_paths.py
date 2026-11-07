#!/usr/bin/env python3
"""Exercise actual cache setup/setter/FFP format paths with mocked filesystem calls."""
import argparse
import pathlib
import re
import subprocess
import tempfile


def definition(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.source_root
    source = (root / "source/vgl.c").read_text()
    globals_ = source[source.index("static GLboolean vgl_inited"):source.index("extern int unsafe_allocator_counter")]
    shader_root = re.search(r"#ifdef HAVE_SHADER_CACHE\nstatic char shader_cache_root.*?\n#endif", source, re.S).group()
    methods = "\n".join(definition(source, sig) for sig in (
        "static void vgl_init_cache_paths(", "GLboolean vglSetCacheRootPath(", "void vglSetShaderCachePath("))
    init = definition(source, "GLboolean vglInitWithCustomSizes(")
    assert init.index("if (vgl_inited)") < init.index("vgl_init_cache_paths();") < init.index("init_gxm();")
    threshold = definition(source, "GLboolean vglInitWithCustomThreshold(")
    assert threshold.index("sceIoMkdir(cache_root, 0777)") < threshold.index("init_gxm();")
    assert "sceRazorGpuCaptureEnableSalvage(vgl_gpu_crash_path);" in (root / "source/gxm.c").read_text()
    assert '{"vglSetCacheRootPath", (void *)vglSetCacheRootPath}' in (root / "source/lookup.c").read_text()
    ffp = (root / "source/ffp.c").read_text()
    formats = re.findall(r'\s*(sprintf\(fname, "%s/[vf]/[^\n]+;)', ffp)
    assert len(formats) == 6 and "ux0:data/shader_cache" not in ffp
    # Compile every FFP format branch, including high texture-unit and disabled-combiner variants.
    format_checks = "\n".join("{" + statement + r'''
        assert(std::string(fname).starts_with(std::string(vgl_ffp_cache_path) + "/"));
        assert(std::strlen(fname) < sizeof(fname));
    }''' for statement in formats)
    program = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#define _VITAGL_H_
using GLboolean = uint8_t;
#define GL_FALSE 0
#define GL_TRUE 1
#define FFP_SHADER_CACHE_MAGIC 31
#define SHADER_CACHE_MAGIC 3
#define WVP_ON_GPU 1
#define vgl_log(...) ((void)0)
#include "vitaGLCache.h"
std::vector<std::string> directories;
bool existingCustom = false;
int sceIoMkdir(const char* path, unsigned) {
    directories.emplace_back(path);
    return existingCustom && std::string(path).ends_with("/v3") ? -1 : 0;
}
int sceAppMgrAppParamGetString(int, int, char* dst, size_t size) {
    assert(size == 12);
    std::strcpy(dst, "OMWVP0001");
    return 0;
}
char vgl_shader_cache_path[256];
char vgl_file_cache_path[256];
''' + globals_ + shader_root + '\n' + methods + r'''
void reset() {
    vgl_inited = GL_FALSE;
    cache_root[0] = 0;
#ifdef HAVE_SHADER_CACHE
    shader_cache_root[0] = 0;
#endif
#ifdef HAVE_RAZOR
    std::strcpy(vgl_gpu_crash_path, "ux0:data/vitagl_gpucrash.sgx");
#endif
    directories.clear(); existingCustom = false;
}
void checkFormats() {
    char fname[256];
    // Individual arguments are cast to the exact printf type for each extracted branch below.
    struct { unsigned long long raw_high = 7, raw = 8; unsigned raw_low = 9; } cmb_mask;
''' + format_checks.replace("vert_shader_mask", "static_cast<unsigned long long>(1)", 1).replace(
        "vert_shader_mask", "static_cast<unsigned>(1)", 1).replace(
        "frag_shader_mask", "static_cast<unsigned long long>(2)", 1).replace(
        "frag_shader_mask", "static_cast<unsigned>(2)", 1).replace(
        "frag_shader_mask", "static_cast<unsigned long long>(2)", 1).replace(
        "frag_shader_mask", "static_cast<unsigned>(2)", 1) + r'''
}
int main() {
    reset();
    assert(!vglSetCacheRootPath(nullptr)); assert(!vglSetCacheRootPath(""));
    assert(!vglSetCacheRootPath(std::string(128, 'x').c_str()));
    assert(cache_root[0] == 0 && directories.empty());
    vgl_init_cache_paths();
    std::vector<std::string> expected;
#ifdef HAVE_TEX_CACHE
    expected.push_back("ux0:data/vgl_cache");
    expected.push_back("ux0:data/vgl_cache/OMWVP0001");
    assert(std::string(vgl_file_cache_path) == expected.back());
#endif
    for (const char* p : {"ux0:data/shader_cache", "ux0:data/shader_cache/v31",
             "ux0:data/shader_cache/v31/v", "ux0:data/shader_cache/v31/f"}) expected.emplace_back(p);
#ifdef HAVE_SHADER_CACHE
    for (const char* p : {"ux0:data/shader_cache/OMWVP0001", "ux0:data/shader_cache/OMWVP0001/v3",
             "ux0:data/shader_cache/OMWVP0001/v3/v", "ux0:data/shader_cache/OMWVP0001/v3/f"}) expected.emplace_back(p);
    assert(std::string(vgl_shader_cache_path) == "ux0:data/shader_cache/OMWVP0001/v3");
#endif
    assert(directories == expected);
    assert(std::string(vgl_ffp_cache_path) == "ux0:data/shader_cache/v31");
    checkFormats();
#ifdef HAVE_SHADER_CACHE
    reset(); vglSetShaderCachePath("ux0:data/legacy-custom"); vgl_init_cache_paths();
    assert(std::string(vgl_shader_cache_path) == "ux0:data/legacy-custom/v3");
#endif
    reset();
    const std::string root = "ux0:data/openmw-perf/gpu-cache";
    assert(vglSetCacheRootPath(root.c_str()));
    assert(directories.empty());
#ifdef HAVE_RAZOR
    assert(std::string(vgl_gpu_crash_path) == root + "/gpucrash.sgx");
#endif
    vglSetShaderCachePath("ux0:data/legacy-custom");
    vgl_init_cache_paths();
    for (const auto& p : directories) assert(p == root || p.starts_with(root + "/"));
    assert(std::string(vgl_ffp_cache_path) == root + "/ffp/v31");
#ifdef HAVE_SHADER_CACHE
    assert(std::string(vgl_shader_cache_path) == root + "/custom/v3");
#endif
#ifdef HAVE_TEX_CACHE
    assert(std::string(vgl_file_cache_path) == root + "/textures");
#endif
    checkFormats();
    vgl_inited = GL_TRUE;
    assert(!vglSetCacheRootPath("ux0:data/unexpected"));
    assert(std::string(cache_root) == root);
    vgl_inited = GL_FALSE;
    std::string maximum(127, 'x');
    assert(vglSetCacheRootPath(maximum.c_str()));
    maximum[0] = 'y'; assert(cache_root[0] == 'x');
    assert(!vglSetCacheRootPath(std::string(128, 'z').c_str()));
    assert(cache_root[0] == 'x');
    directories.clear(); existingCustom = true;
    vgl_init_cache_paths(); checkFormats();
    for (const auto& p : directories) assert(p.size() < 256 && p.starts_with(std::string(cache_root)));
    std::puts("cache roots, initialization boundary, legacy paths and all FFP formats passed");
}
'''
    with tempfile.TemporaryDirectory(prefix="vitagl-cache-test-") as directory:
        tmp = pathlib.Path(directory)
        (tmp / "cache.cpp").write_text(program)
        # The real header is tested; its existing umbrella include is satisfied by this empty fixture.
        (tmp / "vitaGL.h").write_text("")
        for flags in ((), ("HAVE_SHADER_CACHE",), ("HAVE_TEX_CACHE",),
                      ("HAVE_SHADER_CACHE", "HAVE_TEX_CACHE", "HAVE_RAZOR")):
            command = ["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-deprecated-declarations", "-Wno-unused-parameter", "-fsanitize=address,undefined",
                       "-fno-omit-frame-pointer", "-I", str(root / "source"), "-I", str(tmp)]
            command += ["-D" + flag for flag in flags]
            subprocess.run(command + [str(tmp / "cache.cpp"), "-o", str(tmp / "cache")], check=True)
            subprocess.run([str(tmp / "cache")], check=True)


if __name__ == "__main__":
    main()
