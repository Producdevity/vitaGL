#!/usr/bin/env python3
"""Exercise shader diagnostic sites extracted from the actual vitaGL sources."""
from pathlib import Path
import argparse
import os
import re
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--sanitizer", choices=("address", "thread"), default="address")
args = parser.parse_args()
root = args.source_root / "source"


def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def function(text, name):
    match = re.search(r"^[^\n]*\b" + name + r"\([^\n]*\) \{\n.*?^\}", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"Cannot extract {name}")
    return match.group(0)


public = section((root / "vitaGL.h").read_text(), "#define VGL_SHADER_DIAGNOSTICS_ABI_VERSION", "// Overloads the shader cache path")
internal = (root / "shader_diagnostics.h").read_text()
api = re.sub(r'^#include .*\n', '', (root / "shader_diagnostics.c").read_text(), flags=re.M)
shared = (root / "shared.h").read_text()
custom = (root / "custom_shaders.c").read_text()
gxm = (root / "utils/gxm_utils.c").read_text()
ffp = (root / "ffp.c").read_text()
shader_types = section(shared, "// Matrix uniform struct", "#ifdef HAVE_FFP_SHADER_SUPPORT")
program_types = section(custom, "typedef enum {\n\tUNIFORM_DATA", "#if defined(HAVE_VERTEX_LAYOUT_CACHE) && !defined(STRICT_DRAW_COMPLIANCE) && !defined(DRAW_SPEEDHACK)\nenum")
compile_helper = section(custom, "#ifdef HAVE_SHADER_CACHE\n#define vgl_compile_shader", "void reset_custom_shaders")
vertex_cache = section(gxm, "#define VERTEX_PROGRAM_CACHE_WAYS", "void vglVertexProgramCacheInvalidate")
ffp_branches = []
for short in ("vert", "frag"):
    tail = ffp[ffp.index("// Checking if vertex shader requires a recompilation"):]
    block = section(tail, f"\tif (ffp_dirty_{short}) {{", "\t\t// Adding new shader to RAM cache")
    ffp_branches.append(f"static void ffp_{short}_branch(void) {{\n" + block + "\n\t}\n}\n")

prefix = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <pthread.h>
typedef uint8_t GLboolean;
typedef uint32_t GLuint, GLenum;
typedef int GLsizei;
typedef char GLchar;
#define GL_FALSE 0
#define GL_TRUE 1
enum { GL_VERTEX_SHADER=2, GL_FRAGMENT_SHADER, GL_INVALID_OPERATION };
enum { VGL_MODE_POSTPONED, VGL_MODE_SHADER_PAIR, VGL_MODE_IMMEDIATE };
enum { SHARK_VERTEX_SHADER, SHARK_FRAGMENT_SHADER };
enum { SCE_O_RDONLY=1, SCE_O_WRONLY=2, SCE_O_TRUNC=4, SCE_O_CREAT=8, SCE_SEEK_END=1, SCE_SEEK_SET=2 };
enum { SCE_GXM_PARAMETER_CATEGORY_SAMPLER, SCE_GXM_PARAMETER_CATEGORY_UNIFORM,
 SCE_GXM_PARAMETER_CATEGORY_UNIFORM_BUFFER, SCE_GXM_PARAMETER_CATEGORY_ATTRIBUTE };
enum { SCE_SHACCCG_PARAMETERCLASS_MATRIX, SCE_SHACCCG_PARAMETERCLASS_UNIFORMBLOCK };
#define THREAD_SAFE()
#define SET_GL_ERROR(error) return;
#define vgl_log(...) ((void)0)
#define TEXTURE_IMAGE_UNITS_NUM 16
#define VERTEX_ATTRIBS_NUM 16
#define UBOS_NUM 14
#define SCE_GXM_MAX_VERTEX_ATTRIBUTES 16
#define SCE_GXM_MAX_VERTEX_STREAMS 16
#define DISABLE_TEXTURE_COMBINER
#define WVP_ON_GPU 1
#define FFP_SHADER_CACHE_MAGIC 31
#define TEX0_ENV_PASS_COMBINE 0
#define TEX1_ENV_PASS_COMBINE 0
enum { MODULATE, DECAL, BLEND, ADD, REPLACE, COMBINE };
typedef int SceUID, SceGxmParameterCategory, SceGxmOutputRegisterFormat, SceGxmMultisampleMode;
typedef uint32_t SceGxmShaderPatcherId;
typedef struct { uint32_t value; } SceGxmProgram, SceGxmVertexProgram, SceGxmFragmentProgram;
typedef struct { uint32_t regIndex, offset; } SceGxmVertexAttribute;
typedef struct { uint32_t stride; } SceGxmVertexStream;
typedef int SceGxmProgramParameter, SceGxmBlendInfo, SceShaccCgCompileOutput;
typedef void *SceShaccCgParameter;
typedef struct { uint32_t raw; } blend_config;
typedef struct { GLuint idx; char name[64]; } attr_mapping;
static uint64_t clock_reads;
static uint64_t sceKernelGetProcessTimeWide(void) { return ++clock_reads; }
uint32_t vgl_framecount;
'''

stubs = r'''
static void *gxm_shader_patcher;
static int is_shark_online=1, compiler_opts, compiler_fastmath, compiler_fastprecision, compiler_fastint;
static int glsl_sema_mode;
static int io_hits[2], compiler_fails[2], register_fails[2], patch_failure;
static int last_stage, compile_calls[2], register_calls[2], read_opens[2], write_opens;
static int translator_calls, frees, allocations, vertex_patches, fragment_patches, final_reflection;
static char vgl_shader_cache_path[256]="cache";
static char vgl_ffp_cache_path[256]="ffp-cache";
static void *owned[2048];
static SceGxmProgram compiler_program;
static SceGxmVertexProgram vertex_program;
static SceGxmFragmentProgram fragment_program;
static int guarded_copy;
static jmp_buf failed_compile;
static void *vglMalloc(size_t n) {
 void *p=calloc(1,n ? n : 1); assert(p); allocations++;
 for (unsigned i=0;i<2048;i++) if (!owned[i]) { owned[i]=p; return p; }
 abort();
}
static void *vglCalloc(size_t n,size_t sz) { return vglMalloc(n*sz); }
static void vgl_free(void *p) {
 if (!p) return;
 for(unsigned i=0;i<2048;i++) if (owned[i]==p) { owned[i]=NULL; frees++; free(p); return; }
 abort();
}
static void *checked_copy(void *dst,const void *src,size_t n) {
 if (!src && guarded_copy) longjmp(failed_compile,1);
 return memcpy(dst,src,n);
}
#define vgl_fast_memcpy checked_copy
#define sceClibMemset memset
#define sceClibMemcmp memcmp
static uint64_t XXH3_64bits(const void *data,size_t size) {
 const uint8_t *p=data; uint64_t h=0;
 for(size_t i=0;i<size;i++) h=h*31+p[i];
 return (uint32_t)h << 6; /* All keys share a set for eviction coverage. */
}
static int start_shader_compiler(void) { is_shark_online=1; return 1; }
static int sceIoOpen(const char *name,int flags,int mode) {
 if (flags & SCE_O_RDONLY) {
  int stage=strstr(name,"/v/") ? 0 : 1; last_stage=stage; read_opens[stage]++;
  return io_hits[stage] ? 10+stage : -1;
 }
 write_opens++; return 20;
}
static size_t sceIoLseek(int fd,int offset,int mode) { return 2048; }
static int sceIoRead(int fd,void *buf,size_t size) { memset(buf,0,size); return size; }
static void sceIoClose(int fd) {}
static void sceIoMkdir(const char *name,int mode) {}
static int sceIoWrite(int fd,const void *buf,size_t size) { return size; }
static SceGxmProgram *shark_compile_shader_extended(const char *src,uint32_t *size,int stage,int a,int b,int c,int d) {
 assert(src); last_stage=stage; compile_calls[stage]++; *size=sizeof(compiler_program);
 return compiler_fails[stage] ? NULL : &compiler_program;
}
static int sceGxmShaderPatcherRegisterProgram(void *patcher,const SceGxmProgram *prog,SceGxmShaderPatcherId *id) {
 assert(prog); register_calls[last_stage]++; *id=1+last_stage; return register_fails[last_stage] ? -1 : 0;
}
static int sceGxmProgramGetDefaultUniformBufferSize(const SceGxmProgram *prog) { return 0; }
static void *shark_get_internal_compile_output(void) { return NULL; }
static SceShaccCgParameter sceShaccCgGetFirstParameter(void *out) { return NULL; }
static int sceShaccCgGetParameterClass(void *p) { return 0; }
static const char *sceShaccCgGetParameterName(void *p) { return "uniform"; }
static int sceShaccCgGetParameterBufferIndex(void *p) { return 0; }
static void *sceShaccCgGetNextParameter(void *p) { return NULL; }
static void shark_clear_output(void) {}
static SceGxmProgramParameter *sceGxmProgramFindParameterByName(const SceGxmProgram *p,const char *name) { return NULL; }
static uint32_t *vglProgramGetParameterBase(const SceGxmProgram *p) { static uint32_t data[16]; return data; }
static int sceGxmProgramGetParameterCount(const SceGxmProgram *p) {
 assert(vgl_shader_diagnostics.stages[VGL_SHADER_DIAG_CUSTOM_LINK].positive_results==0);
 final_reflection++; return 0;
}
static int sceGxmProgramParameterGetCategory(const SceGxmProgramParameter *p) { return 0; }
static int sceGxmProgramParameterGetContainerIndex(const SceGxmProgramParameter *p) { return 0; }
static int sceGxmProgramParameterGetResourceIndex(const SceGxmProgramParameter *p) { return 0; }
static int sceGxmProgramParameterIsSamplerCube(const SceGxmProgramParameter *p) { return 0; }
static const char *sceGxmProgramParameterGetName(const SceGxmProgramParameter *p) { return "name"; }
static int sceGxmShaderPatcherCreateVertexProgram(void *patcher,SceGxmShaderPatcherId id,const SceGxmVertexAttribute *a,uint32_t an,const SceGxmVertexStream *s,uint32_t sn,SceGxmVertexProgram **out) {
 vertex_patches++; if (patch_failure) return -7; *out=&vertex_program; return 0;
}
static int sceGxmShaderPatcherCreateFragmentProgram(void *patcher,SceGxmShaderPatcherId id,SceGxmOutputRegisterFormat fmt,SceGxmMultisampleMode msaa,const SceGxmBlendInfo *blend,const SceGxmProgram *v,SceGxmFragmentProgram **out) {
 fragment_patches++; if (patch_failure) return -9; *out=&fragment_program; return 0;
}
'''

custom_stubs = r'''
static shader shader_objects[2], *shaders[]={&shader_objects[0],&shader_objects[1]};
static program progs[1];
static void *serialize_shader(void *out,size_t *sz,shader *s,GLboolean bindings) { *sz=4; return vglMalloc(*sz); }
static void glsl_translator_process(shader *s) { translator_calls++; }
static void glsl_translator_set_process(shader *v,shader *f) { translator_calls++; }
static void glBindAttribLocation(GLuint program,GLuint idx,const char *name) {}
static void *get_block_details(block_uniform *b,uint32_t idx) { return NULL; }
static void *has_block_alias(ubo *u,const char *name) { return NULL; }
static void *get_uniform_alias_data_ptr(uniform *u,uint32_t count,const char *name) { return NULL; }
'''

ffp_stubs = r'''
static int ffp_dirty_vert,ffp_dirty_frag;
static uint32_t vert_shader_mask,frag_shader_mask;
static SceGxmProgram *ffp_vertex_program,*ffp_fragment_program;
static SceGxmShaderPatcherId ffp_vertex_program_id,ffp_fragment_program_id;
static uint32_t ffp_vertex_unif_buf_size,ffp_fragment_unif_buf_size;
static void *ffp_vertex_unif_buf,*ffp_fragment_unif_buf;
static const char *ffp_vert_src="shader",*ffp_frag_src="shader";
static const char *modulate_src="m",*decal_src="d",*blend_src="b",*add_src="a",*replace_src="r";
static int alpha_op,base_texture_id;
static struct { int env_mode; } texture_units[16];
static struct { int clip_planes_num,num_textures,has_colors,lights_num,lighting,shading_mode,
 normalize,fixed_mask,pos_fixed_mask,fast_perspective_correction,fog_mode,tex_env_mode_pass0,
 tex_env_mode_pass1,point_sprite,srgb_mode; } mask;
'''

tests = r'''
static void clean_owned(void) { for(unsigned i=0;i<2048;i++) if(owned[i]) vgl_free(owned[i]); }
static void setup(int enabled) {
 clean_owned(); memset(shader_objects,0,sizeof(shader_objects)); memset(progs,0,sizeof(progs));
 memset(io_hits,0,sizeof(io_hits)); memset(compiler_fails,0,sizeof(compiler_fails)); memset(register_fails,0,sizeof(register_fails));
 memset(compile_calls,0,sizeof(compile_calls)); memset(register_calls,0,sizeof(register_calls)); memset(read_opens,0,sizeof(read_opens));
 translator_calls=allocations=frees=write_opens=vertex_patches=fragment_patches=final_reflection=patch_failure=guarded_copy=0;
 vgl_framecount=0; clock_reads=0; glsl_sema_mode=VGL_MODE_IMMEDIATE; is_shark_online=1;
 vglSetShaderDiagnostics(enabled); vglResetShaderDiagnostics();
 for(int i=0;i<2;i++) { shader_objects[i].type=i ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER;
  shader_objects[i].source=vglMalloc(32); strcpy(shader_objects[i].source,"shader"); shader_objects[i].size=6; shader_objects[i].is_glsl=1; }
 progs[0].vshader=&shader_objects[0]; progs[0].fshader=&shader_objects[1];
 vglVertexProgramCacheReset();
}
static void bucket(int stage,uint64_t requests,uint64_t positive,uint64_t negative,int timed) {
 vglShaderDiagnosticStageStats *b=&vgl_shader_diagnostics.stages[stage];
 assert(b->requests==requests && b->positive_results==positive && b->negative_results==negative);
 assert(b->total_us >= b->max_us);
 if (timed && requests) assert(b->total_us && b->max_us);
 else assert(!b->total_us && !b->max_us);
}
static void disabled(void) {
 assert(!clock_reads);
 assert(!vgl_shader_diagnostics.compile_requests && !vgl_shader_diagnostics.postponed_requests);
 assert(!vgl_shader_diagnostics.link_missing_shaders && !vgl_shader_diagnostics.link_relinked);
 for(int i=0;i<VGL_SHADER_DIAG_STAGE_COUNT;i++) bucket(i,0,0,0,0);
}
static void api_cases(void) {
 vglShaderDiagnostics out,unchanged; memset(&out,0xa7,sizeof(out)); unchanged=out;
 assert(!vgl_shader_diagnostics.enabled); assert(!clock_reads);
 assert(!vglGetShaderDiagnostics(&out,sizeof(out)-1,VGL_SHADER_DIAGNOSTICS_ABI_VERSION));
 assert(!vglGetShaderDiagnostics(&out,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION+1));
 assert(!vglGetShaderDiagnostics(&out,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION-1));
 assert(!vglGetShaderDiagnostics(NULL,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION));
 assert(!memcmp(&out,&unchanged,sizeof(out)) && !clock_reads);
 assert(vglGetShaderDiagnostics(&out,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION));
 assert(!out.enabled && !out.snapshot_us && out.size==sizeof(out));
 assert(out.timed_stages==(VGL_SHADER_DIAGNOSTICS_TIMED_STAGES & out.available_stages));
 assert(sizeof(out)==784);
#ifdef HAVE_SHADER_CACHE
 assert(out.available_stages==((1u << VGL_SHADER_DIAG_STAGE_COUNT)-1));
#else
 assert(out.available_stages==(((1u << VGL_SHADER_DIAG_STAGE_COUNT)-1) & ~3u));
#endif
 vglSetShaderDiagnostics(GL_TRUE); vgl_framecount=42;
 vgl_diag_event(VGL_SHADER_DIAG_VERTEX_PATCH,1); vgl_framecount=99;
 vgl_diag_event(VGL_SHADER_DIAG_VERTEX_PATCH,0);
 assert(!clock_reads);
 assert(vglGetShaderDiagnostics(&out,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION));
 assert(out.enabled && out.snapshot_us==1 && out.snapshot_frame==99);
 assert(out.stages[VGL_SHADER_DIAG_VERTEX_PATCH].first_frame==42 && out.stages[VGL_SHADER_DIAG_VERTEX_PATCH].last_frame==99);
 vglResetShaderDiagnostics(); assert(vgl_shader_diagnostics.enabled); bucket(VGL_SHADER_DIAG_VERTEX_PATCH,0,0,0,0);
 assert(vgl_shader_diagnostics.timed_stages==(VGL_SHADER_DIAGNOSTICS_TIMED_STAGES & vgl_shader_diagnostics.available_stages));
 vglSetShaderDiagnostics(GL_FALSE); uint64_t clocks=clock_reads;
 assert(vglGetShaderDiagnostics(&out,sizeof(out),VGL_SHADER_DIAGNOSTICS_ABI_VERSION)); assert(!out.snapshot_us && clock_reads==clocks);
}
#ifdef HAVE_SHADER_CACHE
static const int disk_cache=1;
#else
static const int disk_cache=0;
#endif
static int trace[256][12], trace_index;
static void check_trace(int enabled) {
 int current[]={compile_calls[0],compile_calls[1],register_calls[0],register_calls[1],read_opens[0],read_opens[1],write_opens,translator_calls,allocations,frees,vertex_patches,fragment_patches};
 assert(trace_index<256);
 if(enabled) assert(!memcmp(trace[trace_index],current,sizeof(current)));
 else memcpy(trace[trace_index],current,sizeof(current));
 trace_index++;
}
static void custom_cases(int enabled) {
 for(int stage=0;stage<2;stage++) for(int hit=0;hit<2;hit++) for(int regfail=0;regfail<2;regfail++) {
  setup(enabled); io_hits[stage]=hit; register_fails[stage]=regfail; vgl_framecount=7; int cache_hit=disk_cache && hit;
  glCompileShader(stage+1);
  assert(read_opens[stage]==disk_cache && compile_calls[stage]==!cache_hit && register_calls[stage]==1);
  assert(shader_objects[stage].prog && !shader_objects[stage].source);
  if(enabled) {
   assert(vgl_shader_diagnostics.compile_requests==1);
   bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_CACHE+stage,disk_cache,cache_hit,disk_cache&&!hit,disk_cache);
   bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_COMPILE+stage,!cache_hit,!cache_hit,0,!cache_hit);
   bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_REGISTER+stage,1,!regfail,regfail,0);
   if(disk_cache) assert(vgl_shader_diagnostics.stages[VGL_SHADER_DIAG_CUSTOM_VERTEX_CACHE+stage].first_frame==7);
  } else disabled();
  check_trace(enabled);
 }
 for(int stage=0;stage<2;stage++) {
  setup(enabled); compiler_fails[stage]=1; glCompileShader(stage+1);
  assert(!shader_objects[stage].prog && shader_objects[stage].source && !register_calls[stage]);
  if(enabled) bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_COMPILE+stage,1,0,1,1); else disabled();
 }
 setup(enabled); glsl_sema_mode=VGL_MODE_POSTPONED; glCompileShader(1); glCompileShader(2);
 assert(!compile_calls[0] && !compile_calls[1] && !read_opens[0] && !read_opens[1] && !clock_reads);
 if(enabled) assert(vgl_shader_diagnostics.compile_requests==2 && vgl_shader_diagnostics.postponed_requests==2); else disabled();
 for(int vh=0;vh<2;vh++) for(int fh=0;fh<2;fh++) for(int existing=0;existing<4;existing++) {
  setup(enabled); glsl_sema_mode=VGL_MODE_POSTPONED; io_hits[0]=vh; io_hits[1]=fh;
  for(int i=0;i<2;i++) if(existing & (1<<i)) shader_objects[i].prog=&compiler_program;
  glLinkProgram(1);
  assert(progs[0].status==PROG_LINKED && final_reflection>=2);
  for(int i=0;i<2;i++) {
   int needed=!(existing & (1<<i)),lookup=disk_cache && needed,hit=disk_cache && (i ? fh : vh);
   assert(read_opens[i]==lookup && compile_calls[i]==(needed && !hit));
   if(enabled) { bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_CACHE+i,lookup,lookup&&hit,lookup&&!hit,lookup);
    bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_COMPILE+i,needed&&!hit,needed&&!hit,0,needed&&!hit);
    bucket(VGL_SHADER_DIAG_CUSTOM_VERTEX_REGISTER+i,needed,needed,0,0); }
  }
  if(enabled) bucket(VGL_SHADER_DIAG_CUSTOM_LINK,1,1,0,1); else disabled();
  check_trace(enabled);
 }
#ifndef SKIP_ERROR_HANDLING
 for(int mode=0;mode<2;mode++) for(int stage=0;stage<2;stage++) {
  setup(enabled); glsl_sema_mode=mode ? VGL_MODE_POSTPONED : VGL_MODE_IMMEDIATE;
  vgl_free(shader_objects[stage].source); shader_objects[stage].source=NULL;
  shader_objects[1-stage].prog=&compiler_program; glLinkProgram(1);
  assert(progs[0].status!=PROG_LINKED && !final_reflection);
  if(enabled) { bucket(VGL_SHADER_DIAG_CUSTOM_LINK,1,0,1,1); assert(vgl_shader_diagnostics.link_missing_shaders==1); } else disabled();
  check_trace(enabled);
 }
#endif
 setup(enabled); shader_objects[0].prog=shader_objects[1].prog=&compiler_program; progs[0].status=PROG_LINKED;
 glLinkProgram(1); assert(!final_reflection);
 if(enabled) { bucket(VGL_SHADER_DIAG_CUSTOM_LINK,1,0,1,1); assert(vgl_shader_diagnostics.link_relinked==1); } else disabled();
}
static void ffp_cases(int enabled) {
 for(int stage=0;stage<2;stage++) for(int hit=0;hit<2;hit++) for(int regfail=0;regfail<2;regfail++) {
  setup(enabled); io_hits[stage]=hit; register_fails[stage]=regfail;
  ffp_dirty_vert=ffp_dirty_frag=0; ffp_vert_branch(); ffp_frag_branch();
  assert(!clock_reads && !read_opens[0] && !read_opens[1]);
  if(stage) { ffp_dirty_frag=1; ffp_frag_branch(); } else { ffp_dirty_vert=1; ffp_vert_branch(); }
  assert(read_opens[stage]==1 && compile_calls[stage]==!hit && register_calls[stage]==1);
  if(enabled) { bucket(VGL_SHADER_DIAG_FFP_VERTEX_CACHE+stage,1,hit,!hit,1);
   bucket(VGL_SHADER_DIAG_FFP_VERTEX_COMPILE+stage,!hit,!hit,0,!hit);
   bucket(VGL_SHADER_DIAG_FFP_VERTEX_REGISTER+stage,1,!regfail,regfail,0); } else disabled();
  check_trace(enabled);
 }
 for(int stage=0;stage<2;stage++) {
  setup(enabled); compiler_fails[stage]=1; guarded_copy=1;
  if(!setjmp(failed_compile)) {
   if(stage) { ffp_dirty_frag=1; ffp_frag_branch(); } else { ffp_dirty_vert=1; ffp_vert_branch(); }
   abort();
  }
  assert(!register_calls[stage]);
  if(enabled) bucket(VGL_SHADER_DIAG_FFP_VERTEX_COMPILE+stage,1,0,1,1); else disabled();
 }
}
static void patch_cases(int enabled) {
 setup(enabled); SceGxmVertexAttribute attr={1,0}; SceGxmVertexStream stream={4}; SceGxmVertexProgram *v=NULL;
 assert(!vglCreateVertexProgram(1,&attr,1,&stream,1,&v));
 vglShaderDiagnostics after_miss=vgl_shader_diagnostics;
 assert(!vglCreateVertexProgram(1,&attr,1,&stream,1,&v)); /* table */
 assert(!memcmp(&after_miss,&vgl_shader_diagnostics,sizeof(after_miss)));
 assert(vertex_program_cache_mru_valid);
 assert(!vglCreateVertexProgram(1,&attr,1,&stream,1,&v)); /* MRU */
 assert(!memcmp(&after_miss,&vgl_shader_diagnostics,sizeof(after_miss)));
 assert(vertex_patches==1 && !clock_reads);
 if(enabled) bucket(VGL_SHADER_DIAG_VERTEX_PATCH,1,1,0,0); else disabled();
 for(int i=2;i<=5;i++) assert(!vglCreateVertexProgram(i,&attr,1,&stream,1,&v));
 assert(!vglCreateVertexProgram(1,&attr,1,&stream,1,&v)); /* evicted */
 assert(vertex_patches==6 && !clock_reads);
 patch_failure=1; assert(vglCreateVertexProgram(6,&attr,1,&stream,1,&v)==-7);
 assert(vglCreateVertexProgram(6,&attr,1,&stream,1,&v)==-7); /* failure not cached */
 if(enabled) bucket(VGL_SHADER_DIAG_VERTEX_PATCH,8,6,2,0); else disabled();
 SceGxmFragmentProgram *f=NULL; patch_failure=0;
 patch_fragment_program(1,0,0,NULL,NULL,&f); patch_failure=1;
 patch_fragment_program(1,0,0,NULL,NULL,&f); assert(fragment_patches==2 && clock_reads==(enabled ? 4u : 0u));
 if(enabled) bucket(VGL_SHADER_DIAG_FRAGMENT_PATCH,2,1,1,1); else disabled();
}
static void *snapshot_owner(void *out) {
 vglSetShaderDiagnostics(GL_TRUE); vglResetShaderDiagnostics(); vgl_framecount=123;
 vgl_shader_diagnostics.stages[VGL_SHADER_DIAG_VERTEX_PATCH].requests=UINT64_C(0x100000000);
 assert(vglGetShaderDiagnostics(out,sizeof(vglShaderDiagnostics),VGL_SHADER_DIAGNOSTICS_ABI_VERSION)); return NULL;
}
int main(void) {
 api_cases();
 for(int enabled=0;enabled<2;enabled++) { trace_index=0; custom_cases(enabled); ffp_cases(enabled); patch_cases(enabled); }
 clean_owned(); pthread_t owner; vglShaderDiagnostics snapshot;
 assert(!pthread_create(&owner,NULL,snapshot_owner,&snapshot)); assert(!pthread_join(owner,NULL));
 assert(snapshot.snapshot_frame==123 && snapshot.stages[VGL_SHADER_DIAG_VERTEX_PATCH].requests==UINT64_C(0x100000000));
 puts("shader diagnostics: actual-source custom, FFP, patch cache, disabled and snapshot cases passed");
}
'''

code = "\n".join([prefix, public, internal, api, stubs, shader_types, program_types,
    custom_stubs, section(custom, "#define set_default_attrib_binding()", "#define disable_draw_attrib"),
    function(custom, "unserialize_shader"), compile_helper, function(custom, "glCompileShader"),
    function(custom, "glLinkProgram"), vertex_cache, function(shared, "patch_fragment_program"),
    ffp_stubs, *ffp_branches, tests])
with tempfile.TemporaryDirectory(prefix="vitagl-shader-diag-") as tmp:
    path = Path(tmp)
    (path / "fixture.c").write_text(code)
    for error_flags in ([], ["-DSKIP_ERROR_HANDLING"], ["-DLOG_ERRORS"]):
        for cache in (True, False):
            flags = ["-DHAVE_SHADER_CACHE"] if cache else []
            flags += error_flags
            cmd = shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-O1", "-g",
                "-fsanitize=" + ("address,undefined" if args.sanitizer=="address" else "thread"), "-fno-omit-frame-pointer", "-pthread",
                "-Wno-format", "-Wno-deprecated-declarations", *flags, str(path / "fixture.c"), "-o", str(path / "fixture")]
            subprocess.run(cmd, check=True)
            subprocess.run([str(path / "fixture")], check=True)
