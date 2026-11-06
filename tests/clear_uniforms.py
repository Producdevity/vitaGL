#!/usr/bin/env python3
"""Fault-inject uniform reservations in the actual glClear implementation."""
from pathlib import Path
import argparse
import os
import re
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", nargs="?", type=Path,
                    default=Path(__file__).resolve().parents[1] / "source/misc.c")
source_path = parser.parse_args().source
source = source_path.read_text()
match = re.search(r"^void glClear\(GLbitfield mask\) \{\n.*?^\}", source, re.M | re.S)
if match is None:
    raise SystemExit("Could not isolate glClear")

prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int GLbitfield;
typedef unsigned int GLenum;
typedef float GLfloat;
enum { GL_FALSE, GL_TRUE };
enum { GL_NO_ERROR=0, GL_INVALID_VALUE=0x501, GL_INVALID_OPERATION=0x502, GL_OUT_OF_MEMORY=0x505 };
enum { GL_DEPTH_BUFFER_BIT=0x100, GL_STENCIL_BUFFER_BIT=0x400, GL_COLOR_BUFFER_BIT=0x4000 };
#define SCE_GXM_ERROR_OUT_OF_MEMORY ((int)0x805b0002)
#define SCE_GXM_ERROR_NOT_WITHIN_SCENE ((int)0x805b0006)
#define SCE_GXM_ERROR_RESERVE_FAILED ((int)0x805b000b)
enum { SCE_GXM_CULL_NONE=0, SCE_GXM_CULL_CW=1 };
enum { SCE_GXM_DEPTH_WRITE_DISABLED=0, SCE_GXM_DEPTH_WRITE_ENABLED=1 };
enum { SCE_GXM_POLYGON_MODE_TRIANGLE_FILL=0, SCE_GXM_POLYGON_MODE_TRIANGLE_LINE=1,
       SCE_GXM_POLYGON_MODE_TRIANGLE_POINT=2 };
enum { SCE_GXM_FRAGMENT_PROGRAM_DISABLED=0, SCE_GXM_FRAGMENT_PROGRAM_ENABLED=1 };
enum { SCE_GXM_STENCIL_FUNC_ALWAYS=0, SCE_GXM_STENCIL_OP_KEEP=1, SCE_GXM_STENCIL_OP_REPLACE=2 };
#define THREAD_SAFE()
#define SET_GL_ERROR(value) do { vgl_error=(value); return; } while(0)
#define vgl_log(...) ((void)0)
static void *gxm_context=(void *)(uintptr_t)1;
static int orig_depth_test, depth_test_state, depth_mask_state, is_fbo_float;
static float depth_value;
static int polygon_mode_front=SCE_GXM_POLYGON_MODE_TRIANGLE_LINE;
static int polygon_mode_back=SCE_GXM_POLYGON_MODE_TRIANGLE_POINT;
static int stencil_mask_front_write=0x35, stencil_mask_back_write=0x79, stencil_value=0x1a7;
static int clear_draw_state;
static int clear_vertex_program_patched=11, clear_fragment_program_patched=12,
           clear_fragment_program_float_patched=13;
static int clear_position=1, clear_depth=2, clear_color=3;
static struct { float x,y,z,w; } vertex_values={-1.0f,-1.0f,1.0f,1.0f}, *clear_vertices=&vertex_values;
static struct { float r,g,b,a; } clear_rgba_val={0.125f,0.25f,0.5f,1.0f};
static GLenum vgl_error;
struct Stencil { int function,fail,depth_fail,depth_pass,read_mask,write_mask,reference; };
struct State {
 int viewport,cull,depth_test,depth_write,front_polygon,back_polygon,front_enabled,back_enabled;
 int front_bias[2],back_bias[2];
 struct Stencil front_stencil,back_stencil;
 void *vertex_uniform,*fragment_uniform;
};
static struct State state,initial;
static float vertex_buffer[8],fragment_buffer[4];
static float previous_vertex_buffer[8],previous_fragment_buffer[4];
static int failure_stage,failure_code,null_success;
static int vertex_reserves,fragment_reserves,uniform_writes,draws,bad_order,scene_resets;
static int restored_depth,restored_viewport,restored_cull,restored_stencil,restored_offset;
static int restored_vertex_uniform,restored_fragment_uniform;
static int vertex_program,fragment_program;
static GLbitfield requested_mask;
static void scene_reset(void) { ++scene_resets; }
static void invalidate_viewport(void) { state.viewport=2; }
static void validate_viewport(void) { ++restored_viewport; state.viewport=initial.viewport; }
static void sceGxmSetCullMode(void *context,int mode) { state.cull=mode; }
static void change_cull_mode(void) { ++restored_cull; state.cull=initial.cull; }
static void change_depth_write(int enabled) { state.depth_write=enabled; }
static void invalidate_depth_test(void) {
 orig_depth_test=depth_test_state; depth_test_state=GL_FALSE; state.depth_test=GL_FALSE;
 change_depth_write(SCE_GXM_DEPTH_WRITE_DISABLED);
}
static void validate_depth_test(void) {
 ++restored_depth; depth_test_state=orig_depth_test; state.depth_test=depth_test_state;
 change_depth_write(depth_mask_state && depth_test_state);
}
static void sceGxmSetFrontDepthBias(void *context,int factor,int units) {
 state.front_bias[0]=factor; state.front_bias[1]=units;
}
static void sceGxmSetBackDepthBias(void *context,int factor,int units) {
 state.back_bias[0]=factor; state.back_bias[1]=units;
}
static void update_polygon_offset(void) {
 ++restored_offset;
 memcpy(state.front_bias,initial.front_bias,sizeof(state.front_bias));
 memcpy(state.back_bias,initial.back_bias,sizeof(state.back_bias));
}
static void sceGxmSetFrontPolygonMode(void *context,int mode) { state.front_polygon=mode; }
static void sceGxmSetBackPolygonMode(void *context,int mode) { state.back_polygon=mode; }
static void sceGxmSetVertexProgram(void *context,int program) { vertex_program=program; }
static void sceGxmSetFragmentProgram(void *context,int program) { fragment_program=program; }
static int sceGxmReserveVertexDefaultUniformBuffer(void *context,void **output) {
 ++vertex_reserves;
 if (failure_stage==1) {
  if (null_success) { *output=NULL; return 0; }
  return failure_code;
 }
 *output=vertex_buffer; state.vertex_uniform=vertex_buffer; return 0;
}
static int sceGxmReserveFragmentDefaultUniformBuffer(void *context,void **output) {
 ++fragment_reserves;
 if (uniform_writes) bad_order=1;
 if (failure_stage==2) {
  if (null_success) { *output=NULL; return 0; }
  return failure_code;
 }
 *output=fragment_buffer; state.fragment_uniform=fragment_buffer; return 0;
}
static int sceGxmSetUniformDataF(void *buffer,int parameter,int offset,int count,const float *data) {
 ++uniform_writes;
 if ((failure_stage==1 && vertex_reserves) || (failure_stage==2 && fragment_reserves)) {
  fputs("uniform write reached a failed reservation\n",stderr); exit(20);
 }
 if (vertex_reserves!=1 || fragment_reserves!=1) bad_order=1;
 assert(offset==0);
 if (parameter==clear_position) {
  assert(buffer==vertex_buffer && count==4); memcpy(vertex_buffer,data,4*sizeof(float));
 } else if (parameter==clear_depth) {
  assert(buffer==vertex_buffer && count==1); vertex_buffer[4]=*data;
 } else {
  assert(parameter==clear_color && buffer==fragment_buffer && count==4);
  memcpy(fragment_buffer,data,4*sizeof(float));
 }
 return 0;
}
static void sceGxmSetFrontFragmentProgramEnable(void *context,int enabled) { state.front_enabled=enabled; }
static void sceGxmSetBackFragmentProgramEnable(void *context,int enabled) { state.back_enabled=enabled; }
static void sceGxmSetFrontStencilFunc(void *context,int function,int fail,int depth_fail,int depth_pass,int read_mask,int write_mask) {
 state.front_stencil=(struct Stencil){function,fail,depth_fail,depth_pass,read_mask,write_mask,state.front_stencil.reference};
}
static void sceGxmSetBackStencilFunc(void *context,int function,int fail,int depth_fail,int depth_pass,int read_mask,int write_mask) {
 state.back_stencil=(struct Stencil){function,fail,depth_fail,depth_pass,read_mask,write_mask,state.back_stencil.reference};
}
static void sceGxmSetFrontStencilRef(void *context,int reference) { state.front_stencil.reference=reference; }
static void sceGxmSetBackStencilRef(void *context,int reference) { state.back_stencil.reference=reference; }
static void refresh_stencil_settings(void) {
 ++restored_stencil; state.front_stencil=initial.front_stencil; state.back_stencil=initial.back_stencil;
}
static void vglRestoreVertexUniformBuffer(void) { ++restored_vertex_uniform; state.vertex_uniform=previous_vertex_buffer; }
static void vglRestoreFragmentUniformBuffer(void) { ++restored_fragment_uniform; state.fragment_uniform=previous_fragment_buffer; }
static void check_stencil(struct Stencil *stencil,int mask) {
 assert(stencil->function==SCE_GXM_STENCIL_FUNC_ALWAYS && stencil->read_mask==0xff);
 if (requested_mask & GL_STENCIL_BUFFER_BIT) {
  assert(stencil->fail==SCE_GXM_STENCIL_OP_REPLACE && stencil->depth_fail==SCE_GXM_STENCIL_OP_REPLACE);
  assert(stencil->depth_pass==SCE_GXM_STENCIL_OP_REPLACE && stencil->write_mask==(mask&0xff));
  assert(stencil->reference==(stencil_value&0xff));
 } else {
  assert(stencil->fail==SCE_GXM_STENCIL_OP_KEEP && stencil->depth_fail==SCE_GXM_STENCIL_OP_KEEP);
  assert(stencil->depth_pass==SCE_GXM_STENCIL_OP_KEEP && stencil->write_mask==0xff);
 }
}
static void sceGxmDrawPrecomputed(void *context,int *draw_state) {
 ++draws;
 assert(failure_stage==0 && uniform_writes==3 && draw_state==&clear_draw_state);
 assert(state.viewport==2 && state.cull==SCE_GXM_CULL_NONE && state.depth_test==GL_FALSE);
 assert(state.depth_write==(depth_mask_state && (requested_mask&GL_DEPTH_BUFFER_BIT)!=0));
 assert(state.front_polygon==SCE_GXM_POLYGON_MODE_TRIANGLE_FILL && state.back_polygon==SCE_GXM_POLYGON_MODE_TRIANGLE_FILL);
 assert(state.front_enabled==((requested_mask&GL_COLOR_BUFFER_BIT)!=0));
 assert(state.back_enabled==((requested_mask&GL_COLOR_BUFFER_BIT)!=0));
 assert(state.front_bias[0]==0 && state.front_bias[1]==0 && state.back_bias[0]==0 && state.back_bias[1]==0);
 assert(vertex_program==clear_vertex_program_patched);
 assert(fragment_program==(is_fbo_float ? clear_fragment_program_float_patched : clear_fragment_program_patched));
 assert(memcmp(vertex_buffer,&vertex_values,4*sizeof(float))==0);
 assert(vertex_buffer[4]==depth_value*2-1);
 assert(memcmp(fragment_buffer,&clear_rgba_val,4*sizeof(float))==0);
 check_stencil(&state.front_stencil,stencil_mask_front_write);
 check_stencil(&state.back_stencil,stencil_mask_back_write);
}
'''
suffix = r'''
static void run_case(GLbitfield mask,int stage,int error,int null_buffer,int old_error,int depth_enabled,int depth_mask,int floating) {
 failure_stage=stage; failure_code=error; null_success=null_buffer;
 depth_test_state=depth_enabled; depth_mask_state=depth_mask; is_fbo_float=floating;
 requested_mask=mask; depth_value=0.25f; vgl_error=old_error;
 vertex_reserves=fragment_reserves=uniform_writes=draws=bad_order=scene_resets=0;
 restored_depth=restored_viewport=restored_cull=restored_stencil=restored_offset=0;
 restored_vertex_uniform=restored_fragment_uniform=0;
 memset(&state,0,sizeof(state));
 memset(vertex_buffer,0,sizeof(vertex_buffer)); memset(fragment_buffer,0,sizeof(fragment_buffer));
 state.viewport=1; state.cull=SCE_GXM_CULL_CW; state.depth_test=depth_enabled;
 state.depth_write=depth_enabled && depth_mask; state.front_polygon=polygon_mode_front; state.back_polygon=polygon_mode_back;
 state.front_enabled=state.back_enabled=SCE_GXM_FRAGMENT_PROGRAM_ENABLED;
 state.front_bias[0]=3; state.front_bias[1]=4; state.back_bias[0]=5; state.back_bias[1]=6;
 state.front_stencil=(struct Stencil){8,9,10,11,0x12,0x34,0x56};
 state.back_stencil=(struct Stencil){18,19,20,21,0x22,0x44,0x66};
 state.vertex_uniform=previous_vertex_buffer; state.fragment_uniform=previous_fragment_buffer;
 initial=state;
 glClear(mask);
 assert(scene_resets==1 && vertex_reserves==1);
 assert(fragment_reserves==((stage==1 && !null_buffer) ? 0 : 1));
 assert(restored_depth==1 && restored_viewport==1 && restored_cull==1 && restored_stencil==1 && restored_offset==1);
 assert(restored_vertex_uniform==1 && restored_fragment_uniform==1);
 assert(depth_test_state==depth_enabled && depth_mask_state==depth_mask);
 assert(memcmp(&state,&initial,sizeof(state))==0);
 if (stage) {
  assert(uniform_writes==0 && draws==0);
  GLenum expected=(error==SCE_GXM_ERROR_OUT_OF_MEMORY || error==SCE_GXM_ERROR_RESERVE_FAILED) && !null_buffer
      ? GL_OUT_OF_MEMORY : GL_INVALID_OPERATION;
  assert(vgl_error==(old_error ? (GLenum)old_error : expected));
 } else {
  assert(uniform_writes==3 && draws==1 && bad_order==0);
  assert(vgl_error==(GLenum)old_error);
 }
}
int main(int argc,char **argv) {
 assert(argc==2);
 int stage=0,error=0,null_buffer=0,old_error=0;
 if (!strcmp(argv[1],"vertex-fail")) { stage=1; error=SCE_GXM_ERROR_RESERVE_FAILED; }
 else if (!strcmp(argv[1],"fragment-fail")) { stage=2; error=SCE_GXM_ERROR_RESERVE_FAILED; }
 else if (!strcmp(argv[1],"vertex-oom")) { stage=1; error=SCE_GXM_ERROR_OUT_OF_MEMORY; }
 else if (!strcmp(argv[1],"fragment-oom")) { stage=2; error=SCE_GXM_ERROR_OUT_OF_MEMORY; }
 else if (!strcmp(argv[1],"vertex-scene-error")) { stage=1; error=SCE_GXM_ERROR_NOT_WITHIN_SCENE; }
 else if (!strcmp(argv[1],"fragment-scene-error")) { stage=2; error=SCE_GXM_ERROR_NOT_WITHIN_SCENE; }
 else if (!strcmp(argv[1],"vertex-null")) { stage=1; null_buffer=1; }
 else if (!strcmp(argv[1],"fragment-null")) { stage=2; null_buffer=1; }
 else if (!strcmp(argv[1],"preserve-error")) { stage=2; error=SCE_GXM_ERROR_RESERVE_FAILED; old_error=GL_INVALID_VALUE; }
 else assert(!strcmp(argv[1],"success"));
 for (int bits=1;bits<8;bits++) for(int depth=0;depth<2;depth++) for(int mask=0;mask<2;mask++) for(int floating=0;floating<2;floating++) {
  GLbitfield clear=(bits&1 ? GL_COLOR_BUFFER_BIT:0) | (bits&2 ? GL_DEPTH_BUFFER_BIT:0) | (bits&4 ? GL_STENCIL_BUFFER_BIT:0);
  run_case(clear,stage,error,null_buffer,old_error,depth,mask,floating);
 }
 printf("%s: 56 state/clear combinations passed\n",argv[1]);
 return 0;
}
'''
cases = ["vertex-fail", "fragment-fail", "vertex-oom", "fragment-oom", "vertex-scene-error",
         "fragment-scene-error", "vertex-null", "fragment-null", "preserve-error", "success"]
failures = []
with tempfile.TemporaryDirectory(prefix="vitagl-clear-") as directory:
    work = Path(directory)
    generated = work / "clear.c"
    generated.write_text(prefix + "\n" + match.group() + "\n" + suffix)
    for skip in (False, True):
        mode = "skip-error-handling" if skip else "checked"
        binary = work / mode
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(generated), "-o", str(binary)]
        if skip:
            command.append("-DSKIP_ERROR_HANDLING")
        subprocess.run(command, check=True)
        for case in cases:
            result = subprocess.run([str(binary), case], text=True, capture_output=True)
            if result.returncode:
                failures.append(f"{mode}/{case}")
                print(f"FAIL {mode}/{case}: {result.stderr.strip()}")
            else:
                print(f"PASS {mode}/{result.stdout.strip()}")
if failures:
    raise SystemExit(f"Failed {len(failures)} cases: " + ", ".join(failures))
print("All 20 cases passed under AddressSanitizer and UndefinedBehaviorSanitizer.")
