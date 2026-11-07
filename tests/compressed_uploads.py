#!/usr/bin/env python3
"""Exercise the actual compressed-upload functions with a mock async GXM queue."""
from pathlib import Path
import argparse
import os
import re
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", nargs="?", type=Path,
                    default=Path(__file__).resolve().parents[1] / "source/utils/gpu_utils.c")
source_path = parser.parse_args().source
source = source_path.read_text()
names = [
    "gpu_get_compressed_mip_size", "gpu_get_compressed_mipchain_size",
    "gpu_get_compressed_mip_offset", "gpu_alloc_compressed_cube_texture",
    "gpu_alloc_compressed_texture",
]
functions = []
for name in names:
    match = re.search(r"^[^\n]*\b" + name + r"\([^;\n]*\) \{\n.*?^\}", source, re.M | re.S)
    if match is None:
        raise SystemExit(f"Could not isolate {name}")
    functions.append(match.group())

prefix = r'''
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int GLboolean;
typedef int SceGxmTextureFormat;
enum { GL_FALSE, GL_TRUE };
enum {
 SCE_GXM_TEXTURE_FORMAT_PVRT2BPP_1BGR,
 SCE_GXM_TEXTURE_FORMAT_PVRT2BPP_ABGR,
 SCE_GXM_TEXTURE_FORMAT_PVRT4BPP_1BGR,
 SCE_GXM_TEXTURE_FORMAT_PVRT4BPP_ABGR,
 SCE_GXM_TEXTURE_FORMAT_PVRTII2BPP_ABGR,
 SCE_GXM_TEXTURE_FORMAT_PVRTII4BPP_ABGR,
 SCE_GXM_TEXTURE_FORMAT_UBC1_1BGR,
 SCE_GXM_TEXTURE_FORMAT_UBC1_ABGR,
 SCE_GXM_TEXTURE_FORMAT_ETC1_1BGR,
 SCE_GXM_TEXTURE_FORMAT_UBC4_R,
 SCE_GXM_TEXTURE_FORMAT_UBC2_ABGR,
 SCE_GXM_TEXTURE_FORMAT_UBC3_ABGR,
 SCE_GXM_TEXTURE_FORMAT_UBC5_GR
};
enum { TEX_VALID = 1, OBJ_NOT_USED = 0 };
enum { SCE_GXM_TRANSFER_COLORKEY_NONE, SCE_GXM_TRANSFER_FORMAT_RAW128,
 SCE_GXM_TRANSFER_FORMAT_RAW64, SCE_GXM_TRANSFER_LINEAR, SCE_GXM_TRANSFER_SWIZZLED };
#define MAX(a,b) ((a) > (b) ? (a) : (b))
#define MIN(a,b) ((a) < (b) ? (a) : (b))
#define ALIGNBLOCK(x,a) (((x)+(a)-1)/(a))
#define vgl_fast_memcpy memcpy
#define vgl_memcpy memcpy
#define vgl_memset memset
typedef struct { uint32_t w,h; } SceGxmTexture;
typedef struct {
 int status, faces_counter, mip_count, use_mips, last_frame;
 void *data, *palette_data;
 SceGxmTexture gxm_tex;
} texture;
static int pending, transfer_count, zero_transfers, swizzles, reallocs;
static int enforce_lifetime;
static uint32_t nearest_po2(uint32_t x) { uint32_t r=1; while(r<x) r*=2; return r; }
static void *gpu_alloc_mapped_for_gpu(size_t n) { void *p=malloc(n); assert(p); memset(p,0xcc,n); return p; }
static unsigned char staging[1048576];
static void *gpu_alloc_mapped_temp(size_t n) { assert(n<=sizeof(staging)); return staging; }
static void *vgl_realloc(void *p,size_t n) {
 if (enforce_lifetime && pending) { fputs("pending transfer during destination reallocation\n",stderr); exit(10); }
 reallocs++; return realloc(p,n);
}
static void gpu_free_texture_data(texture *t) {
 if (enforce_lifetime && pending) { fputs("pending transfer during destination release\n",stderr); exit(11); }
 free(t->data); t->data=NULL;
}
static void vglGetTexSizes(SceGxmTexture *t,uint32_t *w,uint32_t *h) { *w=t->w; *h=t->h; }
static void vglInitSwizzledTexture(SceGxmTexture *t,void *p,int f,int w,int h,int m) { t->w=w; t->h=h; }
static void vglInitCubeTexture(SceGxmTexture *t,void *p,int f,int w,int h,int m) { t->w=w; t->h=h; }
static int tex_format_to_alignment(int f) { return f==SCE_GXM_TEXTURE_FORMAT_UBC3_ABGR ? 16:8; }
static void dxt_compress(void *dst,uint8_t *src,uint32_t w,uint32_t h,int alpha) { abort(); }
static int sceGxmTransferCopy(uint32_t w,uint32_t h,int a,int b,int key,
 int sf,int sl,const void *src,int sx,int sy,int ss,int df,int dl,
 void *dst,int dx,int dy,int ds,void *sync,int flags,void *notification) {
 transfer_count++; if(!w||!h) zero_transfers++; pending++; return 0;
}
static int sceGxmTransferFinish(void) { pending=0; return 0; }
static void swizzle(uint8_t *dst,uint8_t *src,int a,int b,int w,int h,int s,int dim) {
 swizzles++; *dst=*src;
}
#define SwizzleTexData128Bpp swizzle
#define SwizzleTexData64Bpp swizzle
#define SwizzleTexDataETC1 swizzle
'''
suffix = r'''
static unsigned char data[65536];
static int formats[] = {SCE_GXM_TEXTURE_FORMAT_UBC1_ABGR,
 SCE_GXM_TEXTURE_FORMAT_UBC2_ABGR,SCE_GXM_TEXTURE_FORMAT_UBC3_ABGR,
 SCE_GXM_TEXTURE_FORMAT_PVRTII2BPP_ABGR};
int main(int argc,char **argv) {
 assert(argc==2); data[0]=0x5a;
 if (!strcmp(argv[1],"mip-tail")) {
  const int dimensions[][2]={{1,1},{2,2},{1,8},{8,1},{2,16},{16,2}};
  for (int f=0;f<4;f++) for(int i=0;i<6;i++) {
   texture t={0};
   int w=dimensions[i][0],h=dimensions[i][1];
   int before=swizzles;
   gpu_alloc_compressed_texture(0,w,h,formats[f],0,data,&t,0,GL_FALSE);
   if(zero_transfers || swizzles==before) { fprintf(stderr,"bad 2D tail format=%d %dx%d\n",f,w,h); return 20; }
   free(t.data);
   memset(&t,0,sizeof(t)); before=swizzles;
   gpu_alloc_compressed_cube_texture(w,h,formats[f],0,data,&t,0,GL_FALSE,0);
   if(zero_transfers || swizzles==before) { fprintf(stderr,"bad cube tail format=%d %dx%d\n",f,w,h); return 21; }
   free(t.data);
  }
 } else if (!strcmp(argv[1],"lifetime")) {
  enforce_lifetime=1;
  for(int f=0;f<4;f++) {
   texture t={0}; t.use_mips=1;
   gpu_alloc_compressed_texture(0,16,16,formats[f],0,data,&t,0,GL_FALSE);
   gpu_alloc_compressed_texture(1,8,8,formats[f],0,data,&t,0,GL_FALSE);
   if(pending) { fputs("upload returned with pending GPU transfer\n",stderr); return 12; }
   gpu_free_texture_data(&t);
   memset(&t,0,sizeof(t));
   for(int face=0;face<6;face++) {
    gpu_alloc_compressed_cube_texture(16,16,formats[f],0,data,&t,0,GL_FALSE,face);
    if(pending) { fputs("cube upload returned with pending transfer\n",stderr); return 13; }
   }
   gpu_free_texture_data(&t);
  }
  assert(reallocs==4);
 } else if (!strcmp(argv[1],"full-block")) {
  for(int f=0;f<4;f++) {
   texture t={0}; int before=transfer_count;
   gpu_alloc_compressed_texture(0,16,16,formats[f],0,data,&t,0,GL_FALSE);
   assert(transfer_count==before+1 && zero_transfers==0); free(t.data); pending=0;
  }
 } else return 2;
 printf("PASS %s: transfers=%d, swizzles=%d, reallocs=%d\n",argv[1],transfer_count,swizzles,reallocs);
}
'''

with tempfile.TemporaryDirectory(prefix="vita-upload-test-") as out:
    out = Path(out)
    cfile = out / "test.c"
    cfile.write_text(prefix + "\n".join(functions) + suffix)
    failed = False
    for direct in (False, True):
        binary = out / ("test-direct" if direct else "test-staged")
        cmd = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-O1", "-g", "-fsanitize=address,undefined",
        ]
        if direct:
            cmd += ["-DTEXTURE_UPLOADS_SPEEDHACK"]
        subprocess.run(cmd + [str(cfile), "-lm", "-o", str(binary)], check=True)
        for case in ("mip-tail", "lifetime", "full-block"):
            result = subprocess.run([str(binary), case])
            print(f"{'direct' if direct else 'staged'} {case}: exit={result.returncode}", flush=True)
            failed |= result.returncode != 0
    raise SystemExit(1 if failed else 0)
