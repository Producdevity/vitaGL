#!/usr/bin/env python3
"""Actual cache implementation and mem_utils wrappers with bounded SDK stubs."""
import argparse
from pathlib import Path
from types import SimpleNamespace
import json, shutil, subprocess
import mspace_stats_probe as base

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sanitize',choices=('none','address,undefined','thread'),default='address,undefined');p.add_argument('--cc',default='clang');a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    base.run(SimpleNamespace(source_root=a.source_root,prepare_only=True,sanitize=a.sanitize),out)
    prefix=(out/'probe_fixture.c').read_text().split('int main(int argc, char **argv) {',1)[0]
    prefix=prefix.replace('static _Thread_local int query_thread;', 'static _Thread_local int query_thread, fault_thread;\nstatic int hold_query_lock, hold_query_unlock, query_at_hold, release_query;\nstatic size_t query_result;')
    prefix=prefix.replace('if (fail_lock) return -1;', 'if (fail_lock || fault_thread) return -1;')
    prefix=prefix.replace('return pthread_mutex_lock(mutex);', '''int result=pthread_mutex_lock(mutex);
    if (query_thread && hold_query_lock) {
        pthread_mutex_lock(&barrier_mutex); query_at_hold=1; pthread_cond_broadcast(&barrier_cond);
        while (!release_query) pthread_cond_wait(&barrier_cond,&barrier_mutex);
        pthread_mutex_unlock(&barrier_mutex);
    }
    return result;''')
    prefix=prefix.replace('int result = pthread_mutex_unlock(mutex);', '''if (query_thread && hold_query_unlock) {
        pthread_mutex_lock(&barrier_mutex); query_at_hold=1; pthread_cond_broadcast(&barrier_cond);
        while (!release_query) pthread_cond_wait(&barrier_cond,&barrier_mutex);
        pthread_mutex_unlock(&barrier_mutex);
    }
    int result = pthread_mutex_unlock(mutex);''')
    prefix=prefix.replace('if (block_alloc) {','if (block_alloc && !fault_thread) {')
    prefix=prefix.replace('(void)vgl_mem_get_free_space(VGL_MEM_RAM);\n\tpthread_mutex_lock(&barrier_mutex);','query_result=vgl_mem_get_free_space(VGL_MEM_RAM);\n\tpthread_mutex_lock(&barrier_mutex);')
    fixture=prefix+Path(__file__).with_name('mspace_stats_cache_fixture.c').read_text()
    (out/'cache_fixture.c').write_text(fixture)
    compiler=shutil.which(a.cc);assert compiler
    flags=['-std=c11','-O1','-g','-pthread','-fno-omit-frame-pointer','-Wall','-Wextra','-Werror','-Wno-unused-function','-I',str(out)]
    if a.sanitize!='none':flags+=['-fsanitize='+a.sanitize]
    subprocess.run([compiler,*flags,str(out/'memory_stats_probe.c'),str(out/'cache_fixture.c'),'-o',str(out/'cache')],check=True)
    cases=('off','reuse','verify','create-failure','lock-failure','unlock-failure','mismatch0','mismatch1','mismatch2','mismatch3','incomplete1','incomplete2','mutator-failure-owned','mutator-failure-waiting','mutator-failure-unlock')
    for case in cases:subprocess.run([str(out/'cache'),case],check=True,timeout=30)
    custom=(out/'custom_fixture.c').read_text().replace('int main(void) {','int main(void) {\n assert(!vglSetMspaceStatsCache(1)); assert(!vglSetMspaceStatsCache(2)); assert(vglSetMspaceStatsCache(0));').replace('assert(stats_calls == 1); return 0;', 'assert(stats_calls == 1); vglMspaceStatsCache c; assert(vglGetMspaceStatsCache(&c,sizeof(c),1)); assert(!c.supported && !c.mode && c.initialized); return 0;')
    (out/'cache_custom.c').write_text(custom)
    subprocess.run([compiler,*flags,'-Wno-unused-parameter','-DHAVE_CUSTOM_HEAP',str(out/'memory_stats_probe.c'),str(out/'cache_custom.c'),'-o',str(out/'custom-cache')],check=True)
    subprocess.run([str(out/'custom-cache')],check=True,timeout=30)
    physical = prefix.split('#include "actual_wrappers.inc"',1)[0] + '''
typedef struct { unsigned size; size_t free_phycont_mem; } SceAppMgrBudgetInfo;
typedef struct { unsigned size; size_t size_phycont; } SceKernelFreeMemorySizeInfo;
static int system_app_mode, physical_calls;
static size_t tm_free[4] = {111,222,333,444};
static void sceAppMgrGetBudgetInfo(SceAppMgrBudgetInfo *info) { ++physical_calls; info->free_phycont_mem=9999; }
static void sceKernelGetFreeMemorySize(SceKernelFreeMemorySizeInfo *info) { ++physical_calls; info->size_phycont=7777; }
''' + base.function((a.source_root/'source/utils/mem_utils.c').read_text(),'vgl_mem_get_free_space') + '''
int main(void) {
#ifdef HAVE_CUSTOM_HEAP
 assert(!vglSetMspaceStatsCache(1));
 const size_t fixed=111+222+444;
#else
 assert(vglSetMspaceStatsCache(1));
 const size_t fixed=980+1970+3950;
#endif
 vgl_mspace_stats_probe_initialize();
 assert(vgl_mem_get_free_space(VGL_MEM_EXTERNAL)==0);
 assert(vgl_mem_get_free_space(VGL_MEM_ALL)==fixed+7777);
 assert(vgl_mem_get_free_space(VGL_MEM_ALL)==fixed+7777);
 assert(physical_calls==2);
 system_app_mode=1;
 assert(vgl_mem_get_free_space(VGL_MEM_ALL)==fixed+9999);
 assert(physical_calls==3);
#ifdef HAVE_CUSTOM_HEAP
 assert(raw_calls==0);
#else
 assert(raw_calls==3);
 vglMspaceStatsCache c;assert(vglGetMspaceStatsCache(&c,sizeof(c),1));assert(c.pools[2].queries==0);
#endif
 return 0;
}
'''
    (out/'physical_fixture.c').write_text(physical)
    for custom_heap in (False,True):
        switches=['-DPHYCONT_ON_DEMAND']+(['-DHAVE_CUSTOM_HEAP','-Wno-unused-parameter']if custom_heap else[])
        binary=out/('physical-custom'if custom_heap else'physical')
        subprocess.run([compiler,*flags,'-Wno-unused-variable',*switches,str(out/'memory_stats_probe.c'),str(out/'physical_fixture.c'),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,timeout=30)
    result=json.loads((out/'result.json').read_text());result.update(mode='validated',cases=[*cases,'custom-heap','phycont-on-demand','phycont-custom-heap'],compiler=compiler)
    result['generated_sha256'].update({name:base.hashlib.sha256((out/name).read_bytes()).hexdigest()
        for name in ('cache_fixture.c','cache_custom.c','physical_fixture.c')})
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('All cache cases passed')
if __name__=='__main__':main()
