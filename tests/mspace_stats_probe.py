#!/usr/bin/env python3
"""Compile actual mspace probe/wrapper code with bounded host SDK stubs."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SHARED_PREFIX = '''#ifndef SHARED_H
#define SHARED_H
#include <stddef.h>
#include <stdint.h>
#include <pthread.h>
#include <stdlib.h>
typedef unsigned char GLboolean;
#define GL_FALSE 0
#define GL_TRUE 1
typedef pthread_mutex_t SceKernelLwMutexWork;
typedef struct { uint32_t capacity, unk, peak_in_use, current_in_use; } SceClibMspaceStats;
typedef struct { SceClibMspaceStats stats; } FakeSpace;
typedef void *SceClibMspace;
int sceKernelCreateLwMutex(SceKernelLwMutexWork *, const char *, unsigned, int, void *);
int sceKernelDeleteLwMutex(SceKernelLwMutexWork *);
int sceKernelLockLwMutex(SceKernelLwMutexWork *, int, void *);
int sceKernelUnlockLwMutex(SceKernelLwMutexWork *, int);
void sceClibMspaceMallocStats(SceClibMspace, SceClibMspaceStats *);
void *sceClibMspaceMalloc(SceClibMspace, size_t);
void *sceClibMspaceCalloc(SceClibMspace, size_t, size_t);
void *sceClibMspaceMemalign(SceClibMspace, size_t, size_t);
void *sceClibMspaceRealloc(SceClibMspace, void *, size_t);
void sceClibMspaceFree(SceClibMspace, void *);
size_t sceClibMspaceMallocUsableSize(void *);
static void *memalign(size_t alignment, size_t size) { (void)alignment; return malloc(size); }
'''

CUSTOM_FIXTURE = '''#include "shared.h"
#include "memory_stats_probe.h"
#include <assert.h>
static int stats_calls;
void sceClibMspaceMallocStats(SceClibMspace space, SceClibMspaceStats *out) { (void)space; (void)out; ++stats_calls; }
int main(void) {
 assert(!vglSetMspaceStatsProbe(1)); assert(vglSetMspaceStatsProbe(0));
 vgl_mspace_stats_probe_initialize();
 vglMspaceStatsProbe s; assert(vglGetMspaceStatsProbe(&s, sizeof(s), 1));
 assert(!s.supported && !s.enabled && s.initialized);
 assert(!vgl_mspace_stats_probe_begin_impl((vglMemType)-1));
 SceClibMspaceStats stats = {0}; vgl_mspace_stats_probe_stats_impl(VGL_MEM_RAM, 0, &stats);
 assert(stats_calls == 1); return 0;
}
'''

def function(text, name):
    match = re.search(r'^(?:size_t|void \*|void )\s*' + name + r'\(', text, re.M)
    if not match:
        raise ValueError(name)
    # These source functions close at column zero; inactive preprocessor arms
    # contain alternative braces and cannot be balanced as a single C body.
    end = re.search(r'^}', text[match.end():], re.M)
    if not end:
        raise ValueError(name + ': missing column-zero closing brace')
    return text[match.start():match.end() + end.end()]

def run(args, out):
    source = args.source_root.resolve() / 'source'
    fixture = Path(__file__).with_name('mspace_stats_probe_fixture.c')
    header = (source / 'vitaGL.h').read_text()
    types = header[header.index('typedef enum {\n\tVGL_MEM_VRAM'):header.index('\ntypedef enum {', header.index('} vglMemType;'))]
    shared = SHARED_PREFIX + types + '\n#endif\n'
    (out / 'shared.h').write_text(shared)
    for name in ('memory_stats_probe.c', 'memory_stats_probe.h'):
        shutil.copyfile(source / name, out / name)
    shutil.copyfile(fixture, out / 'probe_fixture.c')
    mem = (source / 'utils/mem_utils.c').read_text()
    names = ('vgl_mem_get_free_space', 'vgl_free', 'vgl_malloc', 'vgl_calloc', 'vgl_memalign', 'vgl_realloc')
    extracted = {name: function(mem, name) for name in names}
    (out / 'actual_wrappers.inc').write_text('\n\n'.join(extracted.values()) + '\n')
    assert mem.count('sceClibMspaceCreate(') == 2
    assert 'vgl_mspace_stats_probe_initialize();' in function(mem, 'vgl_mem_init')
    assert 'vgl_mspace_stats_probe_begin((vglMemType)i);' in function(mem, 'vgl_mem_init')
    assert 'vgl_mspace_stats_probe_end((vglMemType)i, probe_locked);' in function(mem, 'vgl_mem_init')
    provide = function(mem, 'vgl_mem_provide_phycont')
    assert provide.index('probe_begin(VGL_MEM_PHYCONT)') < provide.index('mempool_size[VGL_MEM_PHYCONT] =')
    assert provide.index('probe_end(VGL_MEM_PHYCONT, probe_locked)') > provide.index('sceClibMspaceCreate(')
    assert 'sceClibMspaceDestroy(' not in mem and 'sceClibMspaceReallocf(' not in mem
    assert 'sceClibMspaceReallocalign(' not in mem
    for name in names[1:]:
        assert extracted[name].count('vgl_mspace_stats_probe_begin(type)') == 1
        assert extracted[name].count('vgl_mspace_stats_probe_end(type, probe_locked)') == 1
    assert 'sceKernelGetProcessTime' not in (source / 'memory_stats_probe.c').read_text()
    print('Actual source hook coverage and no-clock guards passed', flush=True)

    custom = CUSTOM_FIXTURE
    (out / 'custom_fixture.c').write_text(custom)
    result = {
        'mode': 'prepared' if args.prepare_only else 'validated', 'sanitizer': args.sanitize,
        'actual_wrapper_names': list(names),
        'source_sha256': {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (source / 'memory_stats_probe.c', source / 'memory_stats_probe.h',
                                       source / 'vitaGL.h', source / 'utils/mem_utils.c')},
        'generated_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in (out / 'shared.h', out / 'actual_wrappers.inc',
                                          out / 'probe_fixture.c', out / 'custom_fixture.c')},
    }
    if not args.prepare_only:
        compiler = shutil.which(args.cc)
        if not compiler:
            raise RuntimeError('Compiler not found: ' + args.cc)
        flags = ['-std=c11', '-O1', '-g', '-pthread', '-fno-omit-frame-pointer',
                 '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-I', str(out)]
        if args.sanitize != 'none':
            flags += ['-fsanitize=' + args.sanitize]
        subprocess.run([compiler, *flags, str(out / 'memory_stats_probe.c'), str(out / 'probe_fixture.c'),
                        '-o', str(out / 'probe')], check=True)
        cases = ('off', 'enabled', 'create-failure', 'lock-failure', 'unlock-failure')
        for case in cases:
            subprocess.run([str(out / 'probe'), case], check=True, timeout=30)
        subprocess.run([compiler, *flags, '-Wno-unused-parameter', '-DHAVE_CUSTOM_HEAP',
                        str(out / 'memory_stats_probe.c'), str(out / 'custom_fixture.c'),
                        '-o', str(out / 'custom')], check=True)
        subprocess.run([str(out / 'custom')], check=True, timeout=30)
        result.update(cases=[*cases, 'custom-heap'], compiler=compiler,
                      compiler_version=subprocess.check_output([compiler, '--version'], text=True).splitlines()[0])
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='Retain generated fixtures/log metadata here; otherwise use a temporary directory')
    parser.add_argument('--sanitize', choices=('none', 'address,undefined', 'thread'), default='address,undefined')
    parser.add_argument('--cc', default='clang')
    parser.add_argument('--prepare-only', action='store_true', help='Check source and generate fixtures without compiling')
    args = parser.parse_args()
    context = nullcontext(args.output.resolve()) if args.output else tempfile.TemporaryDirectory(prefix='vgl-mspace-probe-')
    with context as value:
        out = Path(value)
        out.mkdir(parents=True, exist_ok=True)
        run(args, out)


if __name__ == '__main__':
    main()
