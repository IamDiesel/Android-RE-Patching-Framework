# -*- coding: utf-8 -*-
# arm64mini — generischer, zielagnostischer AArch64-Nutzermodus-Interpreter.
#
# Uebernommen aus dem lipy-Projekt (src/lipy_gluco/engine/arm64mini.py). Enthaelt KEINE
# Ziel-Spezifika (keine Offsets/Tabellen/Schluessel) — reine CPU+Mem+Block-JIT. Zielspezifischer
# Glue dockt ausschliesslich ueber core.native.iface.TargetPlugin an (siehe engine_arm64.py).
#
# ENTWICKLUNGSSTELLE (ab 2026-11-03): Diese Framework-Datei ist die kanonische, weiter-
# entwickelte Fassung; lipy (Stand 2026-10-30, sha256 8cf7c361...) ist eingefroren.
# Weiterentwicklung erfolgt HIER — aber der oeffentliche KONTRAKT zu lipy MUSS gewahrt
# bleiben (nur ADDITIV erweitern), sonst laeuft die ale-Schicht (ale_pure) nicht mehr:
#   Modul-Namen: Mem, CPU, M64
#   Mem: wb(a,data), rb(a,n), w(a,val,nbytes), pages (zuweisbar)
#   CPU(mem,stub): x[0..30], sp, lr, pc, m, bb, stub, probes, cache; run(entry,args=()), wr(name,val)
#   Block/Stub-Protokoll: bb[pc]->blk(cpu)->next_pc ; stub[pc]->fn(cpu)->next_pc|None (None=>lr)
# Garantie: tests/test_arm64mini_contract.py (Oberflaeche + goldene Vektoren).
# ---------------------------------------------------------------------------
#!/usr/bin/env python3
# ============================================================================
# arm64mini.py — minimaler reiner-Python-ARM64-Interpreter fuer den
# White-Box-Chiffre-Subtree von libb11bb8 (kein unicorn, kein nativer Code).
# LOKAL HALTEN.
# ============================================================================
import struct, re   # capstone wird lazy importiert (nur bei cache-miss in fetch) -> warmer Snapshot laeuft capstone-frei

PAGE=0x1000; M64=(1<<64)-1
class Mem:
    _U32_OK=(memoryview(bytearray(4)).cast('I').itemsize==4)
    _U64_OK=(memoryview(bytearray(8)).cast('Q').itemsize==8)
    def __init__(self): self.pages={}; self.u32={}; self.u64={}
    def _pg(self,a):
        p=a&~(PAGE-1); b=self.pages.get(p)
        if b is None: b=bytearray(PAGE); self.pages[p]=b
        return b,a-p
    def wb(self,a,data):
        n=len(data); p=a&~(PAGE-1); o=a-p
        if o+n<=PAGE:
            b=self.pages.get(p)
            if b is None: b=bytearray(PAGE); self.pages[p]=b
            b[o:o+n]=bytes(data); return
        for i in range(n):
            b,oo=self._pg(a+i); b[oo]=data[i]
    def rb(self,a,n):
        p=a&~(PAGE-1); o=a-p
        if o+n<=PAGE:
            b=self.pages.get(p)
            return bytes(b[o:o+n]) if b is not None else bytes(n)
        out=bytearray(n)
        for i in range(n):
            b,oo=self._pg(a+i); out[i]=b[oo]
        return bytes(out)
    def r(self,a,sz):
        p=a&~(PAGE-1); o=a-p
        if o+sz<=PAGE:
            b=self.pages.get(p)
            return 0 if b is None else int.from_bytes(b[o:o+sz],"little")
        return int.from_bytes(self.rb(a,sz),"little")
    def w(self,a,v,sz):
        p=a&~(PAGE-1); o=a-p
        if o+sz<=PAGE:
            b=self.pages.get(p)
            if b is None: b=bytearray(PAGE); self.pages[p]=b
            b[o:o+sz]=(v&((1<<(8*sz))-1)).to_bytes(sz,"little"); return
        self.wb(a,(v&((1<<(8*sz))-1)).to_bytes(sz,"little"))
    def r8(self,a):
        b=self.pages.get(a&~(PAGE-1)); return 0 if b is None else b[a&(PAGE-1)]
    def w8(self,a,v):
        p=a&~(PAGE-1); b=self.pages.get(p)
        if b is None: b=bytearray(PAGE); self.pages[p]=b
        b[a&(PAGE-1)]=v&0xff
    def r32(self,a):
        o=a&(PAGE-1)
        if o<=PAGE-4:
            p=a&~(PAGE-1); b=self.pages.get(p)
            if b is None: return 0
            if Mem._U32_OK and (o&3)==0:
                u=self.u32.get(p)
                if u is None: u=self.u32[p]=memoryview(b).cast('I')
                return u[o>>2]
            return b[o]|(b[o+1]<<8)|(b[o+2]<<16)|(b[o+3]<<24)
        return self.r(a,4)
    def w32(self,a,v):
        o=a&(PAGE-1)
        if o<=PAGE-4:
            p=a&~(PAGE-1); b=self.pages.get(p)
            if b is None: b=bytearray(PAGE); self.pages[p]=b
            if Mem._U32_OK and (o&3)==0:
                u=self.u32.get(p)
                if u is None: u=self.u32[p]=memoryview(b).cast('I')
                u[o>>2]=v&0xffffffff; return
            b[o]=v&0xff; b[o+1]=(v>>8)&0xff; b[o+2]=(v>>16)&0xff; b[o+3]=(v>>24)&0xff; return
        self.w(a,v,4)
    def r64(self,a):
        o=a&(PAGE-1)
        if o<=PAGE-8:
            p=a&~(PAGE-1); b=self.pages.get(p)
            if b is None: return 0
            if Mem._U64_OK and (o&7)==0:
                u=self.u64.get(p)
                if u is None: u=self.u64[p]=memoryview(b).cast('Q')
                return u[o>>3]
            return int.from_bytes(b[o:o+8],'little')
        return self.r(a,8)
    def w64(self,a,v):
        o=a&(PAGE-1)
        if o<=PAGE-8:
            p=a&~(PAGE-1); b=self.pages.get(p)
            if b is None: b=bytearray(PAGE); self.pages[p]=b
            if Mem._U64_OK and (o&7)==0:
                u=self.u64.get(p)
                if u is None: u=self.u64[p]=memoryview(b).cast('Q')
                u[o>>3]=v&0xffffffffffffffff; return
            b[o:o+8]=(v&0xffffffffffffffff).to_bytes(8,'little'); return
        self.w(a,v,8)

def _sx(v,bits):
    if v & (1<<(bits-1)): return v-(1<<bits)
    return v

# ---- AES primitives for NEON aese/aesmc/aesd/aesimc (ARMv8 crypto ext) ----
def _aes_build_sbox():
    p=1;q=1;sbox=[0]*256
    sbox[0]=0x63
    while True:
        p=p^((p<<1)&0xff)^(0x1b if p&0x80 else 0)
        q^=q<<1; q^=q<<2; q^=q<<4; q&=0xff
        if q&0x80: q^=0x09
        x=q^((q<<1)|(q>>7))^((q<<2)|(q>>6))^((q<<3)|(q>>5))^((q<<4)|(q>>4))
        sbox[p]=(x^0x63)&0xff
        if p==1: break
    return sbox
_AES_SBOX=_aes_build_sbox()
_AES_ISBOX=[0]*256
for _i,_v in enumerate(_AES_SBOX): _AES_ISBOX[_v]=_i
def _gm(a,b):
    p=0
    for _ in range(8):
        if b&1: p^=a
        hi=a&0x80; a=(a<<1)&0xff
        if hi: a^=0x1b
        b>>=1
    return p&0xff
def _aes_shiftrows(s):
    o=bytearray(16)
    for k in range(16):
        r=k&3; c=k>>2; o[k]=s[(((c+r)&3)<<2)|r]
    return o
def _aes_inv_shiftrows(s):
    o=bytearray(16)
    for k in range(16):
        r=k&3; c=k>>2; o[k]=s[(((c-r)&3)<<2)|r]
    return o
def _aes_mixcolumns(s):
    o=bytearray(16)
    for c in range(4):
        a=s[c*4:c*4+4]
        o[c*4+0]=_gm(a[0],2)^_gm(a[1],3)^a[2]^a[3]
        o[c*4+1]=a[0]^_gm(a[1],2)^_gm(a[2],3)^a[3]
        o[c*4+2]=a[0]^a[1]^_gm(a[2],2)^_gm(a[3],3)
        o[c*4+3]=_gm(a[0],3)^a[1]^a[2]^_gm(a[3],2)
    return o
def _aes_inv_mixcolumns(s):
    o=bytearray(16)
    for c in range(4):
        a=s[c*4:c*4+4]
        o[c*4+0]=_gm(a[0],14)^_gm(a[1],11)^_gm(a[2],13)^_gm(a[3],9)
        o[c*4+1]=_gm(a[0],9)^_gm(a[1],14)^_gm(a[2],11)^_gm(a[3],13)
        o[c*4+2]=_gm(a[0],13)^_gm(a[1],9)^_gm(a[2],14)^_gm(a[3],11)
        o[c*4+3]=_gm(a[0],11)^_gm(a[1],13)^_gm(a[2],9)^_gm(a[3],14)
    return o

class CPU:
    def __init__(self, mem, stub=None):
        self.m=mem; self.x=[0]*31; self.sp=0; self.lr=0
        self.n=self.z=self.c=self.vf=0
        self.v=[bytearray(16) for _ in range(32)]   # SIMD/FP register file (128-bit each)
        self.md=None   # lazy: capstone erst bei cache-miss
        self.stub=stub or {}; self.cache={}; self.tcache={}; self.bb={}; self.probes={}
        self.inline_loops=True   # Self-Loop-JIT-Inlining; Analyse/Profiling: False fuer exakte maxins/Block-Counts
        self._REG={}
        for i in range(31):
            self._REG["x%d"%i]=(i,M64); self._REG["w%d"%i]=(i,0xffffffff)
    # ---- SIMD/FP register helpers ----
    def _vinfo(self,tok):
        """Return (index, nbytes) for a SIMD/FP register operand, else None.
        Handles q/d/s/h/b scalar forms and v<N>.<arr> vector forms (full 16B)."""
        t=tok.strip().lower().split(",")[0].strip()
        if not t: return None
        c=t[0]
        if c=='q':
            r=t[1:].split('.')[0]
            if r.isdigit(): return (int(r),16)
        elif c=='v':
            r=t[1:].split('.')[0].split('[')[0]
            if r.isdigit(): return (int(r),16)
        elif c in 'dshb':
            r=t[1:].split('.')[0].split('[')[0]
            if r.isdigit(): return (int(r),{'d':8,'s':4,'h':2,'b':1}[c])
        return None
    def vget(self,idx,nb): return bytes(self.v[idx][:nb])
    def vset(self,idx,data):
        buf=bytearray(16)                 # SIMD loads zero the upper bits of the 128-bit reg
        buf[:len(data)]=bytes(data)[:16]
        self.v[idx]=buf
    # arrangement parse: 'v0.4s'->(idx,eb,n,None) ; 'v0.s[3]'->(idx,eb,None,lane) ; 'q0'->(0,16,1,None)
    def _arr(self,tok):
        t=tok.strip().lower().split(",")[0].strip()
        c=t[0]
        idx=int(t[1:].split('.')[0].split('[')[0])
        if '.' in t:
            arr=t.split('.',1)[1]
            if '[' in arr:
                es=arr[0]; lane=int(arr.split('[')[1].split(']')[0],0)
                return idx,{'b':1,'h':2,'s':4,'d':8}[es],None,lane
            import re as _re; mm=_re.match(r'(\d+)([bhsd])',arr)
            if mm is None:   # bare element letter, e.g. 'v0.s' (count/lane supplied elsewhere)
                return idx,{'b':1,'h':2,'s':4,'d':8}[arr[0]],None,None
            return idx,{'b':1,'h':2,'s':4,'d':8}[mm.group(2)],int(mm.group(1)),None
        return idx,{'q':16,'d':8,'s':4,'h':2,'b':1}.get(c,16),1,None
    def _lanes(self,idx,eb,n):
        raw=self.v[idx]; return [int.from_bytes(raw[i*eb:(i+1)*eb],"little") for i in range(n)]
    def _setlanes(self,idx,eb,n,vals):
        buf=bytearray(16)
        for i in range(n): buf[i*eb:(i+1)*eb]=(vals[i]&((1<<(8*eb))-1)).to_bytes(eb,"little")
        self.v[idx]=buf
    def _neon(self,m,T,o,nxt):
        """Handle SIMD/FP data ops. Return nxt if handled, else None."""
        # fmov between scalar FP and GP / immediate
        if m=="fmov":
            d=self._vinfo(T[0]); s=self._vinfo(T[1]) if len(T)>1 else None
            if d is not None and s is None and not T[1].startswith("#"):   # fmov Sd, Wn / Dd, Xn
                self.vset(d[0], (self.rd(T[1])&((1<<(8*d[1]))-1)).to_bytes(d[1],"little")); return nxt
            if d is None and s is not None:                                # fmov Wd, Sn / Xd, Dn
                self.wr(T[0], int.from_bytes(self.v[s[0]][:self.is32(T[0]) and 4 or 8],"little")); return nxt
            if d is not None and T[1].startswith("#"):                     # fmov Sd/Dd, #imm (rare) -> store raw 0
                self.vset(d[0], b"\x00"*d[1]); return nxt
            return None
        if m in ("st1","ld1","st2","ld2","st3","ld3","st4","ld4","ld1r","ld2r","ld3r","ld4r"):
            addr=self._memaddr(o[o.rindex("["):])
            inner=o[o.index("{")+1:o.index("}")]
            regs=[x.strip() for x in inner.split(",")]
            # lane form uses the index after '}' only when a single reg
            lane=None
            lb=o[o.index("}")+1:]
            if lb.strip().startswith("["):  # e.g. '}[3], [x10]'
                lane=int(lb.split("[")[1].split("]")[0])
            total=0
            for rg in regs:
                si,seb,sn,sl=self._arr(rg)
                cnt = 1 if (sl is not None or lane is not None) else (sn or 1)
                use_lane = sl if sl is not None else lane
                if m.startswith("st"):
                    if use_lane is not None: self.m.wb(addr+total, bytes(self.v[si][use_lane*seb:(use_lane+1)*seb])); total+=seb
                    else: self.m.wb(addr+total, bytes(self.v[si][:seb*cnt])); total+=seb*cnt
                else:  # ld
                    if m.endswith("r"):  # replicate
                        b=self.m.rb(addr+total,seb); self._setlanes(si,seb,sn or (16//seb),[int.from_bytes(b,'little')]*(sn or (16//seb))); total+=seb
                    elif use_lane is not None:
                        self.v[si][use_lane*seb:(use_lane+1)*seb]=self.m.rb(addr+total,seb); total+=seb
                    else:
                        self.vset(si,self.m.rb(addr+total,seb*cnt)); total+=seb*cnt
            return nxt
        if m in ("umov","smov"):   # GP dest from vector lane
            si,seb,sn,sl=self._arr(T[1]); lane=sl if sl is not None else 0
            val=int.from_bytes(self.v[si][lane*seb:(lane+1)*seb],"little")
            if m=="smov": val=_sx(val,8*seb)&(0xffffffff if self.is32(T[0]) else M64)
            self.wr(T[0],val); return nxt
        if self._vinfo(T[0]) is None: return None
        di,eb,n,lane=self._arr(T[0])
        if m=="movi" or m=="mvni": return None  # handled elsewhere
        if m=="dup":
            s=self._vinfo(T[1])
            if s is None:  # dup Vd.Ts, Wn
                val=self.rd(T[1]); self._setlanes(di,eb,n,[val]*n); return nxt
            sd,seb,sn,sl=self._arr(T[1]); src=self._lanes(sd,seb,sl+1)[sl] if sl is not None else self._lanes(sd,eb,1)[0]
            self._setlanes(di,eb,n,[src]*n); return nxt
        if m in ("and","orr","eor","bic","orn") and self._vinfo(T[1]) is not None and len(T)>2 and self._vinfo(T[2]) is not None:
            a=self.v[self._arr(T[1])[0]]; b=self.v[self._arr(T[2])[0]]
            r=bytearray(16)
            for i in range(16):
                if m=="and": r[i]=a[i]&b[i]
                elif m=="orr": r[i]=a[i]|b[i]
                elif m=="eor": r[i]=a[i]^b[i]
                elif m=="bic": r[i]=a[i]&(~b[i]&0xff)
                else: r[i]=a[i]|(~b[i]&0xff)
            self.v[di]=r; return nxt
        if m in ("add","sub","mul") and self._vinfo(T[1]) is not None:
            la=self._lanes(self._arr(T[1])[0],eb,n); lb=self._lanes(self._arr(T[2])[0],eb,n); msk=(1<<(8*eb))-1
            if m=="add": r=[(x+y)&msk for x,y in zip(la,lb)]
            elif m=="sub": r=[(x-y)&msk for x,y in zip(la,lb)]
            else: r=[(x*y)&msk for x,y in zip(la,lb)]
            self._setlanes(di,eb,n,r); return nxt
        if m in ("mla","mls") and self._vinfo(T[1]) is not None:
            la=self._lanes(self._arr(T[1])[0],eb,n); lb=self._lanes(self._arr(T[2])[0],eb,n)
            ld=self._lanes(di,eb,n); msk=(1<<(8*eb))-1
            if m=="mla": r=[(d+x*y)&msk for d,x,y in zip(ld,la,lb)]
            else: r=[(d-x*y)&msk for d,x,y in zip(ld,la,lb)]
            self._setlanes(di,eb,n,r); return nxt
        if m=="neg":
            la=self._lanes(self._arr(T[1])[0],eb,n); msk=(1<<(8*eb))-1
            self._setlanes(di,eb,n,[(-x)&msk for x in la]); return nxt
        if m in ("not","mvn"):
            a=self.v[self._arr(T[1])[0]]; self.v[di]=bytearray((~x)&0xff for x in a); return nxt
        if m in ("shl","sshr","ushr","sshl","ushl"):
            la=self._lanes(self._arr(T[1])[0],eb,n); msk=(1<<(8*eb))-1; bits=8*eb
            if m in ("shl",):
                sh=int(T[2].strip()[1:],0); r=[(x<<sh)&msk for x in la]
            elif m in ("ushr",):
                sh=int(T[2].strip()[1:],0); r=[(x>>sh) for x in la]
            elif m in ("sshr",):
                sh=int(T[2].strip()[1:],0); r=[(_sx(x,bits)>>sh)&msk for x in la]
            else:  # ushl/sshl: per-lane variable shift, amount = signed low byte of Vm lane
                lb=self._lanes(self._arr(T[2])[0],eb,n); r=[]
                for x,s in zip(la,lb):
                    s8=_sx(s&0xff,8)
                    if m=="ushl": r.append(((x<<s8)&msk) if s8>=0 else (x>>(-s8)))
                    else:
                        xs=_sx(x,bits); r.append(((xs<<s8)&msk) if s8>=0 else ((xs>>(-s8))&msk))
                r=[v&msk for v in r]
            self._setlanes(di,eb,n,r); return nxt
        if m in ("ushll","ushll2","sshll","sshll2","uxtl","uxtl2","sxtl","sxtl2"):
            di,ebd,nd,_=self._arr(T[0]); si,ebs,ns,_=self._arr(T[1])          # Widening: schmal -> breit (2x)
            sh=int(T[2].strip()[1:],0) if (m[:4] in ("ushl","sshl") and len(T)>2) else 0  # uxtl/sxtl = ushll/sshll #0
            signed=m.startswith(("sshll","sxtl")); mskd=(1<<(8*ebd))-1; bsrc=8*ebs
            src=self._lanes(si,ebs,ns); lanes=src[ns-nd:] if m.endswith("2") else src[:nd]  # "2" = obere Haelfte
            out=[(((_sx(x,bsrc) if signed else x)<<sh)&mskd) for x in lanes]
            self._setlanes(di,ebd,nd,out); return nxt
        if m=="st1":
            # st1 {v0.s}[3], [xN]  or st1 {v0.16b}, [xN]
            addr=self._memaddr(o[o.index("["):])
            inner=o[o.index("{")+1:o.index("}")].strip()
            si,seb,sn,sl=self._arr(inner)
            if sl is not None:
                self.m.wb(addr, bytes(self.v[si][sl*seb:(sl+1)*seb]))
            else:
                self.m.wb(addr, bytes(self.v[si][:(seb*(sn or 1))]))
            return nxt
        if m=="ld1":
            addr=self._memaddr(o[o.index("["):])
            inner=o[o.index("{")+1:o.index("}")].strip()
            si,seb,sn,sl=self._arr(inner)
            if sl is not None:
                b=self.m.rb(addr,seb); self.v[si][sl*seb:(sl+1)*seb]=b
            else:
                nb=seb*(sn or 1); self.vset(si,self.m.rb(addr,nb))
            return nxt
        if m in ("umov","smov"):
            si,seb,sn,sl=self._arr(T[1]); val=int.from_bytes(self.v[si][sl*seb:(sl+1)*seb],"little")
            if m=="smov": val=_sx(val,8*seb)&M64
            self.wr(T[0],val); return nxt
        if m=="ins":
            # ins Vd.Ts[i], Wn  or ins Vd.Ts[i], Vn.Ts[j]
            si=self._vinfo(T[1])
            if lane is None: di,eb,n,lane=self._arr(T[0])
            if si is None:
                self.v[di][lane*eb:(lane+1)*eb]=(self.rd(T[1])&((1<<(8*eb))-1)).to_bytes(eb,"little")
            else:
                sd,seb,sn,sl=self._arr(T[1]); self.v[di][lane*eb:(lane+1)*eb]=self.v[sd][sl*seb:(sl+1)*seb]
            return nxt
        if m in ("sri","sli"):
            sh=int(T[2].strip()[1:],0); la=self._lanes(self._arr(T[1])[0],eb,n); ld=self._lanes(di,eb,n)
            msk=(1<<(8*eb))-1; r=[]
            for x,dcur in zip(la,ld):
                if m=="sri":
                    ins=(x>>sh); keep=dcur & (~((msk>>sh)) & msk); r.append((keep|ins)&msk)
                else:
                    ins=(x<<sh)&msk; keep=dcur & (~(((msk<<sh)&msk)) & msk); r.append((keep|ins)&msk)
            self._setlanes(di,eb,n,r); return nxt
        if m in ("cmeq","cmhi","cmhs","cmgt","cmge","cmtst"):
            msk=(1<<(8*eb))-1; la=self._lanes(self._arr(T[1])[0],eb,n)
            if len(T)>2 and self._vinfo(T[2]) is not None: lb=self._lanes(self._arr(T[2])[0],eb,n)
            else: lb=[0]*n
            r=[]
            for x,y in zip(la,lb):
                if m=="cmeq": c=(x==y)
                elif m=="cmhi": c=(x>y)
                elif m=="cmhs": c=(x>=y)
                elif m=="cmtst": c=((x&y)!=0)
                elif m=="cmgt": c=(_sx(x,8*eb)>_sx(y,8*eb))
                else: c=(_sx(x,8*eb)>=_sx(y,8*eb))
                r.append(msk if c else 0)
            self._setlanes(di,eb,n,r); return nxt
        if m=="cnt":
            a=self.v[self._arr(T[1])[0]]; self.v[di]=bytearray(bin(x).count("1") for x in a); return nxt
        if m in ("addp","addv","uaddlv","saddlv"):
            la=self._lanes(self._arr(T[1])[0],eb,(16//eb) if eb*n<16 and m!="addp" else n)
            if m=="addp":
                lb=self._lanes(self._arr(T[2])[0],eb,n); cat=la+lb; msk=(1<<(8*eb))-1
                r=[(cat[2*i]+cat[2*i+1])&msk for i in range(len(cat)//2)]
                self._setlanes(di,eb,len(r),r); return nxt
            s=sum(la)
            # addv -> same element width; uaddlv -> widened (2x). write scalar in lane0
            if m=="addv": ob=eb
            else: ob=eb*2
            self.v[di]=bytearray(16); self.v[di][:ob]=(s&((1<<(8*ob))-1)).to_bytes(ob,"little"); return nxt
        if m in ("tbl","tbx"):
            # tbl Vd.Tb, {Vn.16b(,Vm.16b...)}, Vidx.Tb
            tbls=o[o.index("{")+1:o.index("}")]
            tregs=[self._arr(x.strip())[0] for x in tbls.split(",")]
            tab=bytearray()
            for ti in tregs: tab+=self.v[ti]
            idxreg=self._arr(T[-1])[0]; idxb=self.v[idxreg]
            nb=eb*n if eb*n<=16 else 16
            cur=bytearray(self.v[di]); out=bytearray(16)
            for i in range(nb):
                bi=idxb[i]
                if bi<len(tab): out[i]=tab[bi]
                else: out[i]=cur[i] if m=="tbx" else 0
            for i in range(nb,16): out[i]=0
            self.v[di]=out; return nxt
        if m=="ext":
            a=self.v[self._arr(T[1])[0]]; b=self.v[self._arr(T[2])[0]]; idx=int(T[3].strip()[1:],0)
            cat=bytes(a)+bytes(b); width=eb*n
            self.v[di]=bytearray(cat[idx:idx+width])+bytearray(16-width); return nxt
        if m in ("zip1","zip2","uzp1","uzp2","trn1","trn2"):
            la=self._lanes(self._arr(T[1])[0],eb,n); lb=self._lanes(self._arr(T[2])[0],eb,n)
            if m=="zip1":
                r=[];
                for i in range(n//2): r+= [la[i],lb[i]]
            elif m=="zip2":
                r=[];
                for i in range(n//2,n): r+=[la[i],lb[i]]
            elif m=="uzp1": r=[la[i] for i in range(0,n,2)]+[lb[i] for i in range(0,n,2)]
            elif m=="uzp2": r=[la[i] for i in range(1,n,2)]+[lb[i] for i in range(1,n,2)]
            elif m=="trn1":
                r=[0]*n
                for i in range(0,n,2): r[i]=la[i]; r[i+1]=lb[i]
            else:
                r=[0]*n
                for i in range(0,n,2): r[i]=la[i+1]; r[i+1]=lb[i+1]
            self._setlanes(di,eb,n,r[:n]); return nxt
        if m in ("rev16","rev32","rev64"):
            a=bytes(self.v[self._arr(T[1])[0]]); grp={"rev16":2,"rev32":4,"rev64":8}[m]
            out=bytearray(16)
            for base in range(0,16,grp): out[base:base+grp]=a[base:base+grp][::-1]
            self.v[di]=out; return nxt
        if m in ("abs",):
            la=self._lanes(self._arr(T[1])[0],eb,n); msk=(1<<(8*eb))-1
            self._setlanes(di,eb,n,[abs(_sx(x,8*eb))&msk for x in la]); return nxt
        if m in ("bsl","bit","bif"):
            d=bytearray(self.v[di]); a=self.v[self._arr(T[1])[0]]; b=self.v[self._arr(T[2])[0]]
            r=bytearray(16)
            for i in range(16):
                if m=="bsl": r[i]=(b[i]&~d[i])|(a[i]&d[i])
                elif m=="bit": r[i]=(d[i]&~b[i])|(a[i]&b[i])
                else: r[i]=(d[i]&b[i])|(a[i]&~b[i])
            self.v[di]=r; return nxt
        if m=="aese":   # AddRoundKey(eor) then SubBytes then ShiftRows
            st=bytearray(x^y for x,y in zip(self.v[di],self.v[self._arr(T[1])[0]]))
            st=bytes(_AES_SBOX[b] for b in st)
            self.v[di]=bytearray(_aes_shiftrows(st)); return nxt
        if m=="aesmc":
            self.v[di]=bytearray(_aes_mixcolumns(bytes(self.v[self._arr(T[1])[0]]))); return nxt
        if m=="aesd":
            st=bytearray(x^y for x,y in zip(self.v[di],self.v[self._arr(T[1])[0]]))
            st=bytes(_AES_ISBOX[b] for b in _aes_inv_shiftrows(st))
            self.v[di]=bytearray(st); return nxt
        if m=="aesimc":
            self.v[di]=bytearray(_aes_inv_mixcolumns(bytes(self.v[self._arr(T[1])[0]]))); return nxt
        return None
    # register by name
    def rd(self,nm):
        r=self._REG.get(nm)
        if r is not None:
            n,msk=r; return (self.lr if n==30 else self.x[n])&msk
        nm=nm.strip().lower()
        r=self._REG.get(nm)
        if r is not None:
            n,msk=r; return (self.lr if n==30 else self.x[n])&msk
        if nm in ("xzr","wzr"): return 0
        if nm in ("sp","wsp","xsp"): return self.sp&M64
        n=int(nm[1:]); v=self.x[n]
        return v&0xffffffff if nm[0]=='w' else v&M64
    def wr(self,nm,val):
        r=self._REG.get(nm)
        if r is not None:
            n,msk=r; val&=msk
            if n==30: self.lr=val
            else: self.x[n]=val
            return
        nm=nm.strip().lower()
        if nm in ("xzr","wzr"): return
        if nm in ("sp","wsp","xsp"): self.sp=val&M64; return
        r=self._REG.get(nm)
        if r is not None:
            n,msk=r; val&=msk
            if n==30: self.lr=val
            else: self.x[n]=val
            return
        n=int(nm[1:])
        self.x[n]=(val&0xffffffff) if nm[0]=='w' else val&M64
    def is32(self,nm): return nm.strip().lower()[0]=='w'
    def fetch(self,pc):
        ins=self.cache.get(pc)
        if ins is None:
            if self.md is None:
                import capstone; self.md=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_LITTLE_ENDIAN)
            d=next(self.md.disasm_lite(bytes(self.m.rb(pc,4)),pc))   # (addr,size,mnemonic,op_str) -> picklable Tupel
            ins=(d[0],d[2],d[3]); self.cache[pc]=ins
        return ins
    def run(self, entry, args=(), maxins=2000000000):
        import gc
        SENT=0xdead0000
        for i,a in enumerate(args):
            if i<31: self.x[i]=a&M64
        self.lr=SENT; self.pc=entry; c=0
        bb=self.bb; stub=self.stub; probes=self.probes
        has_probes=bool(probes)            # P2: Hoist statt Per-Block-Truthiness
        gcwas=gc.isenabled()
        if gcwas: gc.disable()             # P2: kein zyklischer GC-Scan waehrend des Laufs
        try:
            while True:
                pc=self.pc
                if pc==SENT: return self.x[0]
                if has_probes and pc in probes: probes[pc](self)
                fn=stub.get(pc)
                if fn is not None:
                    nxt=fn(self); self.pc=(nxt if type(nxt) is int else self.lr); continue
                blk=bb.get(pc)
                if blk is None:
                    blk=self._compile_block(pc); bb[pc]=blk
                self.pc=blk(self); c+=1
                if c>maxins: raise Exception("maxins@0x%x"%self.pc)
        finally:
            if gcwas: gc.enable()
    # ===== Basic-Block-JIT: heisse GP-Instruktionen -> direktes Python; Rest -> step() =====
    _JTERM=("b","bl","blr","br","ret","cbz","cbnz","tbz","tbnz","brk","svc","hlt")
    _CONDX={"eq":"(cpu.z==1)","ne":"(cpu.z==0)","cs":"(cpu.c==1)","hs":"(cpu.c==1)",
            "cc":"(cpu.c==0)","lo":"(cpu.c==0)","mi":"(cpu.n==1)","pl":"(cpu.n==0)",
            "vs":"(cpu.vf==1)","vc":"(cpu.vf==0)","hi":"(cpu.c==1 and cpu.z==0)",
            "ls":"(cpu.c==0 or cpu.z==1)","ge":"(cpu.n==cpu.vf)","lt":"(cpu.n!=cpu.vf)",
            "gt":"(cpu.z==0 and cpu.n==cpu.vf)","le":"(cpu.z==1 or cpu.n!=cpu.vf)",
            "al":"True","nv":"True"}
    def _condx(self,cc): return self._CONDX.get(cc.strip().lower())
    def _opsplit(self,o):
        toks=[]; depth=0; cur=""
        for ch in o:
            if ch=='[': depth+=1
            elif ch==']': depth-=1
            if ch==',' and depth==0: toks.append(cur); cur=""
            else: cur+=ch
        if cur.strip()!="" or toks: toks.append(cur)
        T=[t.strip() for t in toks]
        _SH=("lsl","lsr","asr","ror","uxtb","uxth","uxtw","uxtx","sxtb","sxth","sxtw","sxtx")
        merged=[]
        for t in T:
            w0=t.split()[0] if t else ""
            if merged and w0 in _SH: merged[-1]=merged[-1]+", "+t
            else: merged.append(t)
        return merged
    def _ev(self,tok):
        tok=tok.strip()
        if tok[:1]=="#":
            if "," in tok:
                base,shp=tok.split(",",1)
                try: v=int(base.strip()[1:],0); sh=int(shp.strip().split("#")[-1],0)
                except: return None
                return str((v<<sh)&M64)
            try: return str(int(tok[1:],0)&M64)
            except: return None
        low=tok.lower()
        if low in ("xzr","wzr"): return "0"
        if "," in tok:   # shifted/extended register -> direkt emittieren (sonst _regexpr-Fallback)
            base,shp=[p.strip() for p in tok.split(",",1)]
            bl=base.lower()
            if bl in ("xzr","wzr"): bv="0"; w=bl[0]=='w'
            elif bl[:1] in ("x","w"):
                try: n=int(bl[1:])
                except: return "cpu._regexpr(%r)"%tok
                if n>29: return "cpu._regexpr(%r)"%tok
                w=bl[0]=='w'; bv=("(x[%d]&0xffffffff)"%n) if w else ("x[%d]"%n)
            else: return "cpu._regexpr(%r)"%tok
            op=shp.split(); kind=op[0].lower(); sh=int(op[1][1:],0) if len(op)>1 else 0
            if kind=="lsl": e="((%s)<<%d)"%(bv,sh)
            elif kind=="lsr": e="((%s)>>%d)"%(bv,sh)
            elif kind=="asr": e="(_sx(%s,%d)>>%d)"%(bv,32 if w else 64,sh)
            elif kind=="uxtw": e="(((%s)&0xffffffff)<<%d)"%(bv,sh)
            elif kind=="uxtb": e="(((%s)&0xff)<<%d)"%(bv,sh)
            elif kind=="uxth": e="(((%s)&0xffff)<<%d)"%(bv,sh)
            elif kind=="uxtx": e="((%s)<<%d)"%(bv,sh)
            elif kind=="sxtw": e="(_sx((%s)&0xffffffff,32)<<%d)"%(bv,sh)
            elif kind=="sxtb": e="(_sx((%s)&0xff,8)<<%d)"%(bv,sh)
            elif kind=="sxth": e="(_sx((%s)&0xffff,16)<<%d)"%(bv,sh)
            else: return "cpu._regexpr(%r)"%tok
            return "((%s)&M64)"%e
        if low in ("sp","wsp","xsp","x30","w30"): return "cpu._regexpr(%r)"%tok
        if low[:1] not in ("x","w"): return None
        try: n=int(low[1:])
        except: return None
        if n>29: return "cpu._regexpr(%r)"%tok
        return "(x[%d]&0xffffffff)"%n if low[0]=="w" else "x[%d]"%n
    def _dv(self,tok,expr):
        low=tok.strip().lower()
        if low in ("xzr","wzr"): return "pass"
        if low in ("sp","wsp","xsp","x30","w30"): return None
        if low[:1] not in ("x","w"): return None
        try: n=int(low[1:])
        except: return None
        if n>29: return None
        return ("x[%d]=(%s)&0xffffffff"%(n,expr)) if low[0]=="w" else ("x[%d]=(%s)&M64"%(n,expr))
    def _addr(self,o):
        "liefert (addr_expr, pre_stmts). pre_stmts setzen ggf. _ea + Base-Writeback (pre/post-index)."
        i=o.find("[")
        if i<0: return None
        j=o.find("]",i)
        if j<0: return None
        rest=o[j+1:].strip()
        inside=o[i+1:j]; parts=inside.split(",",1)
        base=parts[0].strip(); bl=base.lower()
        if bl[:1]=="x":
            try: bn=int(bl[1:])
            except: return None
            if bn<=29: baseexpr="x[%d]"%bn; basewr="x[%d]"%bn
            elif bn==30: baseexpr="cpu.lr"; basewr="cpu.lr"
            else: return None
        elif bl in ("sp","wsp","xsp"): baseexpr="cpu.sp"; basewr="cpu.sp"
        else: return None
        if rest.startswith(","):   # post-index [xn], #imm
            pm=rest[1:].strip()
            if not pm.startswith("#"): return None
            try: pimm=int(pm[1:],0)
            except: return None
            return ("_ea", ["_ea=(%s)&M64"%baseexpr, "%s=((%s)+(%d))&M64"%(basewr,baseexpr,pimm)])
        if len(parts)==1:
            eff="((%s)&M64)"%baseexpr
        else:
            p2=parts[1].strip()
            if p2.startswith("#"):
                try: disp=int(p2[1:],0)
                except: return None
                eff=("((%s)+(%d))&M64"%(baseexpr,disp)) if disp else ("((%s)&M64)"%baseexpr)
            else:
                off=self._ev(p2)
                if off is None: return None
                eff="((%s)+(%s))&M64"%(baseexpr,off)
        if rest.startswith("!"):   # pre-index [xn, #imm]!
            return ("_ea", ["_ea=%s"%eff, "%s=_ea"%basewr])
        return (eff, [])
    def _emit(self,ins):
        m=ins[1]; o=ins[2]; T=self._opsplit(o)
        w32=lambda t:t.strip().lower()[:1]=='w'
        if m in ("nop","hint","isb","dmb","dsb","sev","sevl","wfe","yield","csdb","pssbb","prfm","prfum"): return ["pass"]
        if m in ("adrp","adr"):
            try: v=int(T[1].strip()[1:],0)&M64
            except: return None
            s=self._dv(T[0],str(v)); return None if s is None else [s]
        if m in ("mov","movz"):
            if "." in T[0] or (len(T)>1 and "." in T[1]): return None
            v=self._ev(T[1])
            if v is None: return None
            s=self._dv(T[0],v); return None if s is None else [s]
        if m=="movk":
            t1=T[1]
            try:
                if "lsl" in t1: v=int(t1.split(",")[0].strip()[1:],0); sh=int(t1.split("#")[-1],0)
                else: v=int(t1.strip()[1:],0); sh=0
            except: return None
            low=T[0].strip().lower()
            if low[:1] not in ("x","w"): return None
            try: n=int(low[1:])
            except: return None
            if n>29: return None
            DM="0xffffffff" if low[0]=="w" else "M64"
            return ["x[%d]=((x[%d] & (~(0xffff<<%d) & %s))|((%d&0xffff)<<%d))&%s"%(n,n,sh,DM,v,sh,DM)]
        if m in ("add","sub"):
            if "." in T[0]: return None
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"(%s)%s(%s)"%(a,("+" if m=="add" else "-"),b)); return None if s is None else [s]
        if m in ("madd","msub"):
            a=self._ev(T[1]); b=self._ev(T[2]); c=self._ev(T[3])
            if a is None or b is None or c is None: return None
            s=self._dv(T[0],"(%s)%s(%s)*(%s)"%(c,("+" if m=="madd" else "-"),a,b)); return None if s is None else [s]
        if m in ("mul","mneg"):
            if "." in T[0]: return None
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            e=("(%s)*(%s)"%(a,b)) if m=="mul" else ("-((%s)*(%s))"%(a,b))
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("and","orr","eor","bic"):
            if "." in T[0]: return None
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            if m=="and": e="(%s)&(%s)"%(a,b)
            elif m=="orr": e="(%s)|(%s)"%(a,b)
            elif m=="eor": e="(%s)^(%s)"%(a,b)
            else: e="(%s)&~(%s)"%(a,b)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("lsl","lsr","asr"):
            a=self._ev(T[1])
            if a is None: return None
            bits=32 if w32(T[0]) else 64
            if T[2].strip().startswith("#"):
                try: sh=str(int(T[2].strip()[1:],0))
                except: return None
            else:
                sv=self._ev(T[2])
                if sv is None: return None
                sh="((%s)&%d)"%(sv,bits-1)
            if m=="lsl": e="(%s)<<%s"%(a,sh)
            elif m=="lsr": e="(%s)>>%s"%(a,sh)
            else: e="(_sx(%s,%d)>>%s)"%(a,bits,sh)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("ubfx","ubfiz","sbfiz","sbfx","bfi","bfxil"):
            try: lsb=int(T[2].strip()[1:],0); width=int(T[3].strip()[1:],0)
            except: return None
            src=self._ev(T[1])
            if src is None: return None
            mask=(1<<width)-1; w=w32(T[0])
            if m in ("bfi","bfxil"):
                low=T[0].strip().lower()
                if low[:1] not in ("x","w"): return None
                try: n=int(low[1:])
                except: return None
                if n>29: return None
                DM="0xffffffff" if w else "M64"
                if m=="bfi": return ["x[%d]=((x[%d] & ~(%d<<%d))|(((%s)&%d)<<%d))&%s"%(n,n,mask,lsb,src,mask,lsb,DM)]
                return ["x[%d]=((x[%d] & ~%d)|(((%s)>>%d)&%d))&%s"%(n,n,mask,src,lsb,mask,DM)]
            if m=="ubfx": e="((%s)>>%d)&%d"%(src,lsb,mask)
            elif m=="ubfiz": e="((%s)&%d)<<%d"%(src,mask,lsb)
            elif m=="sbfiz": e="(_sx((%s)&%d,%d)<<%d)"%(src,mask,width,lsb)
            else: e="_sx(((%s)>>%d)&%d,%d)"%(src,lsb,mask,width)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("uxtb","uxth","uxtw","sxtb","sxth","sxtw"):
            v=self._ev(T[1])
            if v is None: return None
            if m=="uxtb": e="(%s)&0xff"%v
            elif m=="uxth": e="(%s)&0xffff"%v
            elif m=="uxtw": e="(%s)&0xffffffff"%v
            elif m=="sxtb": e="_sx((%s)&0xff,8)"%v
            elif m=="sxth": e="_sx((%s)&0xffff,16)"%v
            else: e="_sx((%s)&0xffffffff,32)"%v
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("neg","mvn"):
            if "." in T[0]: return None
            v=self._ev(T[1])
            if v is None: return None
            e=("-(%s)"%v) if m=="neg" else ("~(%s)"%v)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m=="cmp":
            a=self._ev(T[0]); b=self._ev(T[1])
            if a is None or b is None: return None
            return ["cpu._setflags_sub(%s,%s,%s)"%(a,b,"True" if w32(T[0]) else "False")]
        if m=="cmn":
            a=self._ev(T[0]); b=self._ev(T[1])
            if a is None or b is None: return None
            return ["cpu._setflags_sub(%s,(-(%s))&M64,%s)"%(a,b,"True" if w32(T[0]) else "False")]
        if m=="subs":
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"cpu._setflags_sub(%s,%s,%s)"%(a,b,"True" if w32(T[0]) else "False"))
            return None if s is None else [s]
        if m in ("ldr","ldrb","ldrh","ldrsw","ldrsh","ldrsb","ldur","ldurb","ldurh","ldursw","ldursh","ldursb"):
            r=self._addr(o)
            if r is None: return None
            addr,pre=r
            if m in ("ldrb","ldurb"): e="m.r8(%s)"%addr
            elif m in ("ldrh","ldurh"): e="m.r(%s,2)"%addr
            elif m in ("ldrsw","ldursw"): e="_sx(m.r(%s,4),32)"%addr
            elif m in ("ldrsh","ldursh"): e="_sx(m.r(%s,2),16)"%addr
            elif m in ("ldrsb","ldursb"): e="_sx(m.r8(%s),8)"%addr
            else: e=("m.r32(%s)"%addr) if w32(T[0]) else ("m.r64(%s)"%addr)
            s=self._dv(T[0],e); return None if s is None else pre+[s]
        if m in ("str","strb","strh","stur","sturb","sturh"):
            v=self._ev(T[0])
            if v is None: return None
            r=self._addr(o)
            if r is None: return None
            addr,pre=r
            if m in ("strb","sturb"): st="m.w8(%s,(%s))"%(addr,v)
            elif m in ("strh","sturh"): st="m.w(%s,(%s),2)"%(addr,v)
            else: st=("m.w32(%s,(%s))"%(addr,v)) if w32(T[0]) else ("m.w64(%s,(%s))"%(addr,v))
            return pre+[st]
        if m in ("csel","csinc","csinv","csneg"):
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            cx=self._condx(T[3]) or "cpu._cond(%r)"%T[3].strip()
            if m=="csel": e="(%s) if %s else (%s)"%(a,cx,b)
            elif m=="csinc": e="(%s) if %s else ((%s)+1)"%(a,cx,b)
            elif m=="csinv": e="(%s) if %s else (~(%s))"%(a,cx,b)
            else: e="(%s) if %s else (-(%s))"%(a,cx,b)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("cset","csetm"):
            cx=self._condx(T[1]) or "cpu._cond(%r)"%T[1].strip(); val="M64" if m=="csetm" else "1"
            s=self._dv(T[0],"%s if %s else 0"%(val,cx)); return None if s is None else [s]
        if m=="ldp":
            if "." in T[0] or "." in T[1]: return None
            r=self._addr(o)
            if r is None: return None
            addr,pre=r; sz=4 if w32(T[0]) else 8
            if sz==8: s0=self._dv(T[0],"m.r64(_ea)"); s1=self._dv(T[1],"m.r64(_ea+8)")
            else: s0=self._dv(T[0],"m.r(_ea,%d)"%sz); s1=self._dv(T[1],"m.r(_ea+%d,%d)"%(sz,sz))
            return None if (s0 is None or s1 is None) else pre+["_ea=%s"%addr, s0, s1]
        if m=="stp":
            if "." in T[0] or "." in T[1]: return None
            a=self._ev(T[0]); b=self._ev(T[1])
            if a is None or b is None: return None
            r=self._addr(o)
            if r is None: return None
            addr,pre=r; sz=4 if w32(T[0]) else 8
            if sz==8: return pre+["m.w64(%s,(%s))"%(addr,a),"m.w64((%s)+8,(%s))"%(addr,b)]
            return pre+["m.w(%s,(%s),%d)"%(addr,a,sz),"m.w((%s)+%d,(%s),%d)"%(addr,sz,b,sz)]
        if m in ("umull","umaddl","umsubl"):
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            e="((%s)&0xffffffff)*((%s)&0xffffffff)"%(a,b)
            if m!="umull":
                c=self._ev(T[3])
                if c is None: return None
                e="(%s)%s%s"%(c,("+" if m=="umaddl" else "-"),e)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m in ("smull","smaddl","smsubl"):
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            e="_sx((%s)&0xffffffff,32)*_sx((%s)&0xffffffff,32)"%(a,b)
            if m!="smull":
                c=self._ev(T[3])
                if c is None: return None
                e="_sx(%s,64)%s%s"%(c,("+" if m=="smaddl" else "-"),e)
            s=self._dv(T[0],e); return None if s is None else [s]
        if m=="umulh":
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"((%s)*(%s))>>64"%(a,b)); return None if s is None else [s]
        if m=="smulh":
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"(_sx(%s,64)*_sx(%s,64))>>64"%(a,b)); return None if s is None else [s]
        if m=="adds":
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"cpu._setflags_add(%s,%s,%s)"%(a,b,"True" if w32(T[0]) else "False"))
            return None if s is None else [s]
        if m in ("adc","adcs","sbc","sbcs"):
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            sub="True" if m in ("sbc","sbcs") else "False"; setf="True" if m.endswith("s") else "False"
            s=self._dv(T[0],"cpu._adc(%s,%s,%s,%s,%s)"%(a,b,sub,setf,"True" if w32(T[0]) else "False"))
            return None if s is None else [s]
        if m=="ands":
            a=self._ev(T[1]); b=self._ev(T[2])
            if a is None or b is None: return None
            s=self._dv(T[0],"cpu._andflags(%s,%s,%s)"%(a,b,"True" if w32(T[0]) else "False"))
            return None if s is None else [s]
        if m=="tst":
            a=self._ev(T[0]); b=self._ev(T[1])
            if a is None or b is None: return None
            return ["cpu._andflags(%s,%s,%s)"%(a,b,"True" if w32(T[0]) else "False")]
        if m=="extr":
            hi=self._ev(T[1]); lo=self._ev(T[2])
            if hi is None or lo is None: return None
            try: lsb=int(T[3].strip()[1:],0)
            except: return None
            bits=32 if w32(T[0]) else 64
            s=self._dv(T[0],"((((%s)<<%d)|(%s))>>%d)&%d"%(hi,bits,lo,lsb,(1<<bits)-1))
            return None if s is None else [s]
        if m=="ror":
            if not T[2].strip().startswith("#"): return None
            v=self._ev(T[1])
            if v is None: return None
            bits=32 if w32(T[0]) else 64; sh=int(T[2].strip()[1:],0)%bits
            s=self._dv(T[0],"((%s)>>%d)|((%s)<<%d)"%(v,sh,v,bits-sh))
            return None if s is None else [s]
        return None
    def _emit_term(self,ins,nextpc):
        m=ins[1]; T=self._opsplit(ins[2])
        try:
            if m=="b": return "return %d"%int(T[0][1:],0)
            if m[:2]=="b.":
                cx=self._condx(m[2:]) or "cpu._cond(%r)"%m[2:]
                return "return (%d if %s else %d)"%(int(T[0][1:],0),cx,nextpc)
            if m in ("cbz","cbnz"):
                v=self._ev(T[0])
                if v is None: return None
                return "return (%d if (%s)%s0 else %d)"%(int(T[1][1:],0),v,("==" if m=="cbz" else "!="),nextpc)
            if m in ("tbz","tbnz"):
                v=self._ev(T[0])
                if v is None: return None
                return "return (%d if (((%s)>>%d)&1)%s0 else %d)"%(int(T[2][1:],0),v,int(T[1][1:],0),("==" if m=="tbz" else "!="),nextpc)
            if m=="ret": return "return cpu.lr"
            if m=="bl": return "cpu.lr=%d; return %d"%(nextpc,int(T[0][1:],0))
            if m=="blr":
                v=self._ev(T[0]); return None if v is None else "cpu.lr=%d; return %s"%(nextpc,v)
            if m=="br":
                v=self._ev(T[0]); return None if v is None else "return %s"%v
        except Exception:
            return None
        return None
    def _selfloop_cond(self,ins,pc0):
        "Continue-Bedingung, falls ins ein bedingter Sprung zurueck auf pc0 ist (Self-Loop); sonst None."
        m=ins[1]
        try:
            T=self._opsplit(ins[2])
            if m[:2]=="b.":
                if int(T[0][1:],0)!=pc0: return None
                return self._condx(m[2:]) or "cpu._cond(%r)"%m[2:]
            if m in ("cbz","cbnz"):
                if int(T[1][1:],0)!=pc0: return None
                v=self._ev(T[0]); return None if v is None else "((%s)%s0)"%(v,"==" if m=="cbz" else "!=")
            if m in ("tbz","tbnz"):
                if int(T[2][1:],0)!=pc0: return None
                v=self._ev(T[0]); return None if v is None else "((((%s)>>%d)&1)%s0)"%(v,int(T[1][1:],0),"==" if m=="tbz" else "!=")
        except Exception:
            return None
        return None
    def _compile_block(self,pc0):
        insns=[]; stmts=[]; pc=pc0; term=False; loop_cont=None; loop_next=0
        while True:
            ins=self.fetch(pc); k=len(insns); insns.append(ins)
            m=ins[1]
            if m in self._JTERM or m[:2]=="b.":
                cont=self._selfloop_cond(ins,pc0) if self.inline_loops else None
                if cont is not None:
                    loop_cont=cont; loop_next=pc+4; term=True; break
                ts=self._emit_term(ins,pc+4)
                stmts.append(" "+(ts if ts is not None else "return cpu.step(INS[%d])"%k)); term=True; break
            body=self._emit(ins)
            if body is None: stmts.append(" cpu.step(INS[%d])"%k)
            else:
                for s in body: stmts.append(" "+s)
            pc+=4
            if len(insns)>=400:
                stmts.append(" return %d"%pc); term=True; break
        if loop_cont is not None:
            inner="".join("  "+st[1:]+"\n" for st in stmts)
            src=("def _blk(cpu):\n x=cpu.x; m=cpu.m\n while True:\n"+inner
                 +"  if %s: continue\n  return %d\n"%(loop_cont,loop_next))
        else:
            src="def _blk(cpu):\n x=cpu.x; m=cpu.m\n"+"\n".join(stmts)+("\n" if term else "\n return %d\n"%pc)
        if getattr(self,"bbsrc",None) is not None: self.bbsrc[pc0]=src   # bit-neutraler Code-Dump-Hook
        g={"M64":M64,"_sx":_sx,"INS":insns}
        exec(src,g)
        return g["_blk"]
    # ---- shifted/extended register value from a token like 'x3, lsl #11' ----
    def _regexpr(self,tok):
        # tok may be 'x3' | 'x3, lsl #11' | 'w3, uxtw' | 'w3, uxtw #2' | 'x3, lsr #8' | 'x3, asr #n'
        parts=[p.strip() for p in tok.split(",")]
        v=self.rd(parts[0]); w=self.is32(parts[0])
        if len(parts)==1: return v
        op=parts[1].split()
        kind=op[0]
        sh=int(op[1][1:],0) if len(op)>1 else 0
        if kind=="lsl": v=(v<<sh)
        elif kind=="lsr": v=(v>>sh)
        elif kind=="asr": v=(_sx(v,32 if w else 64)>>sh)
        elif kind=="uxtw": v=(v&0xffffffff)<<sh
        elif kind=="uxtb": v=(v&0xff)<<sh
        elif kind=="uxth": v=(v&0xffff)<<sh
        elif kind=="sxtw": v=(_sx(v&0xffffffff,32)<<sh)&M64
        elif kind=="ror":
            bits=32 if w else 64; sh%=bits; v=((v>>sh)|(v<<(bits-sh)))&((1<<bits)-1)
        elif kind=="sxtb": v=(_sx(v&0xff,8)<<sh)&M64
        elif kind=="sxth": v=(_sx(v&0xffff,16)<<sh)&M64
        elif kind=="uxtx": v=(v<<sh)&M64
        else: raise Exception("shift "+kind)
        return v&M64
    def _imm(self,tok):
        tok=tok.strip()
        return int(tok[1:],0) if tok.startswith("#") else None
    def _memaddr(self,ops):
        # ops is remainder string starting with '['; returns (addr, writeback_reg, wb_val, used_len)
        # handle [xn], [xn,#i], [xn,#i]!, [xn],#i (post), [xn,xm...],
        s=ops.strip()
        assert s[0]=="["
        close=s.index("]")
        inside=s[1:close]; rest=s[close+1:].strip()
        pre=False; post=None
        if rest.startswith("!"): pre=True
        # post-index: '], #imm'
        if rest.startswith(",") :
            post=self._imm(rest[1:].strip())
        parts=[p.strip() for p in inside.split(",",1)]
        base=parts[0]; baseval=self.rd(base)
        addr=baseval; disp=0
        if len(parts)==2:
            p2=parts[1].strip()
            if p2.startswith("#"):
                disp=int(p2[1:],0)
            else:
                disp=self._regexpr(p2)
        if post is not None:
            eff=baseval
            self.wr(base,(baseval+post)&M64)
            return eff
        eff=(baseval+disp)&M64
        if pre: self.wr(base,eff)
        return eff
    def _setflags_sub(self,a,b,w):
        bits=32 if w else 64; mask=(1<<bits)-1
        res=(a-b)&mask
        self.z=1 if res==0 else 0
        self.n=1 if (res>>(bits-1))&1 else 0
        self.c=1 if (a&mask)>=(b&mask) else 0
        sa=(a>>(bits-1))&1; sb=(b>>(bits-1))&1; sr=(res>>(bits-1))&1
        self.vf=1 if (sa!=sb and sa!=sr) else 0
        return res
    def _setflags_add(self,a,b,w):
        bits=32 if w else 64; mask=(1<<bits)-1; res=(a+b)&mask
        self.z=1 if res==0 else 0; self.n=(res>>(bits-1))&1
        self.c=1 if ((a&mask)+(b&mask))>mask else 0
        sa=(a>>(bits-1))&1; sb=(b>>(bits-1))&1; sr=(res>>(bits-1))&1
        self.vf=1 if (sa==sb and sa!=sr) else 0
        return res
    def _adc(self,a,b,sub,setf,w):
        bits=32 if w else 64; mask=(1<<bits)-1
        if sub:
            res=(a-b-(1-self.c))&mask; cout=1 if (a&mask)>=((b&mask)+(1-self.c)) else 0
        else:
            res=(a+b+self.c)&mask; cout=1 if ((a&mask)+(b&mask)+self.c)>mask else 0
        if setf:
            self.z=1 if res==0 else 0; self.n=(res>>(bits-1))&1; self.c=cout
        return res
    def _andflags(self,a,b,w):
        bits=32 if w else 64; r=(a&b)&((1<<bits)-1)
        self.z=1 if r==0 else 0; self.n=(r>>(bits-1))&1
        return a&b
    def step(self,ins):
        pc=ins[0]; nxt=pc+4; m=ins[1]; o=ins[2]
        T=self.tcache.get(pc)
        if T is None:
            # split top-level operands (not inside [])
            toks=[]; depth=0; cur=""
            for ch in o:
                if ch=='[': depth+=1
                elif ch==']': depth-=1
                if ch==',' and depth==0: toks.append(cur); cur=""
                else: cur+=ch
            if cur.strip()!="" or toks: toks.append(cur)
            T=[t.strip() for t in toks]
            # merge shift/extend suffixes (e.g. 'lsl #11', 'uxtw', 'uxtw #2') back onto prev operand
            _SH=("lsl","lsr","asr","ror","uxtb","uxth","uxtw","uxtx","sxtb","sxth","sxtw","sxtx")
            merged=[]
            for t in T:
                w0=t.split()[0] if t else ""
                if merged and w0 in _SH: merged[-1]=merged[-1]+", "+t
                else: merged.append(t)
            T=merged; self.tcache[pc]=T
        def imm(t):
            t=t.strip()
            if "," in t:  # '#0x90, lsl #12'
                base,shp=t.split(",",1); val=int(base.strip()[1:],0)
                sh=int(shp.strip().split("#")[-1],0); return (val<<sh)&M64
            return int(t[1:],0)
        # ---- NEON/SIMD data ops dispatched before GP handlers (eor/and/add/mov collide by mnemonic) ----
        if m in ("fmov","dup","neg","not","mvn","shl","ushr","sshr","ushl","sshl","ushll","ushll2","sshll","sshll2","uxtl","uxtl2","sxtl","sxtl2","sri","sli","st1","ld1","umov","smov","ins","abs","cmeq","cmtst","cmhi","cmhs","cmgt","cmge","rev16","rev32","rev64","tbl","tbx","ext","zip1","zip2","uzp1","uzp2","trn1","trn2","cnt","addp","uaddlv","saddlv","addv","aese","aesmc","aesd","aesimc","bsl","bit","bif") \
           or (m in ("and","orr","eor","bic","orn","add","sub","mul","mla","mls") and self._vinfo(T[0]) is not None):
            r=self._neon(m,T,o,nxt)
            if r is not None: return r
        if m=="adrp":
            self.wr(T[0], int(T[1][1:],0)&M64); return nxt
        if m=="adr":
            self.wr(T[0], int(T[1][1:],0)&M64); return nxt
        if m in ("mov","movz"):
            # SIMD/FP forms: vector reg-reg copy, or GP<->lane moves
            if self._vinfo(T[0]) is not None or (len(T)>1 and self._vinfo(T[1]) is not None):
                di=self._vinfo(T[0]); si=self._vinfo(T[1]) if len(T)>1 else None
                dlane="[" in T[0]; slane=(len(T)>1 and "[" in T[1])
                if di is not None and si is not None:          # mov Vd..., Vn...
                    if dlane or slane:                         # element move: copy ONE lane, rest of Vd unchanged
                        dd,deb,dn,dl=self._arr(T[0]); sd,seb,sn,sl=self._arr(T[1])
                        dl=dl or 0; sl=sl or 0; eb=deb if dlane else seb
                        self.v[dd[0] if isinstance(dd,tuple) else dd][dl*eb:(dl+1)*eb]=self.v[sd[0] if isinstance(sd,tuple) else sd][sl*eb:(sl+1)*eb]
                        return nxt
                    buf=bytearray(16); buf[:]=self.v[si[0]]; self.v[di[0]]=buf; return nxt   # full reg copy
                if di is not None and si is None:              # mov Vd.<ts>[i], Xn  -> write lane from GP
                    dd,eb,dn,lane=self._arr(T[0]); lane=lane or 0
                    val=self.rd(T[1]); self.v[dd][lane*eb:(lane+1)*eb]=(val&((1<<(8*eb))-1)).to_bytes(eb,"little"); return nxt
                if di is None and si is not None:              # mov Xd, Vn.<ts>[i]  -> read lane to GP (umov-like)
                    sd,seb,sn,sl=self._arr(T[1]); lane=sl if sl is not None else 0
                    self.wr(T[0], int.from_bytes(self.v[sd][lane*seb:(lane+1)*seb],"little")); return nxt
            rhs=T[1]
            if rhs.startswith("#"): self.wr(T[0],imm(rhs)&M64)
            else: self.wr(T[0], self._regexpr(rhs))
            return nxt
        if m=="movk":
            sh=0; t1=T[1]
            if "lsl" in t1:
                v=int(t1.split(",")[0].strip()[1:],0); sh=int(t1.split("#")[-1],0)
            else: v=int(t1.strip()[1:],0)
            cur=self.rd(T[0]); mask=~(0xffff<<sh) & (0xffffffff if self.is32(T[0]) else M64)
            self.wr(T[0],(cur&mask)|((v&0xffff)<<sh)); return nxt
        if m in ("add","sub"):
            a=self.rd(T[1]);
            b=imm(T[2]) if T[2].startswith("#") else self._regexpr(T[2])
            # add can have 'lsl #12' shifted imm already handled via regexpr? imm with 'lsl' e.g. '#0x90, lsl #12'
            if T[2].startswith("#") and len(T)>3 and "lsl" in T[3]:
                b=b<<int(T[3].split("#")[-1],0)
            r=(a+b) if m=="add" else (a-b)
            self.wr(T[0], r&M64); return nxt
        if m=="subs":
            a=self.rd(T[1]); b=imm(T[2]) if T[2].startswith("#") else self._regexpr(T[2])
            res=self._setflags_sub(a,b,self.is32(T[0])); self.wr(T[0],res); return nxt
        if m=="adds":
            a=self.rd(T[1]); b=imm(T[2]) if T[2].startswith("#") else self._regexpr(T[2])
            bits=32 if self.is32(T[0]) else 64; mask=(1<<bits)-1; res=(a+b)&mask
            self.z=1 if res==0 else 0; self.n=(res>>(bits-1))&1
            self.c=1 if ((a&mask)+(b&mask))>mask else 0
            sa=(a>>(bits-1))&1; sb=(b>>(bits-1))&1; sr=(res>>(bits-1))&1
            self.vf=1 if (sa==sb and sa!=sr) else 0
            self.wr(T[0],res); return nxt
        if m in ("adc","adcs","sbc","sbcs","ngc","ngcs"):
            bits=32 if self.is32(T[0]) else 64; mask=(1<<bits)-1
            if m in ("ngc","ngcs"): a=0; b=self.rd(T[1])
            else: a=self.rd(T[1]); b=self.rd(T[2])
            if m in ("adc","adcs"): res=(a+b+self.c)&mask; cout=1 if ((a&mask)+(b&mask)+self.c)>mask else 0
            else: res=(a-b-(1-self.c))&mask; cout=1 if (a&mask)>=((b&mask)+(1-self.c)) else 0
            self.wr(T[0],res)
            if m.endswith("s"):
                self.z=1 if res==0 else 0; self.n=(res>>(bits-1))&1; self.c=cout
            return nxt
        if m=="cmp":
            a=self.rd(T[0]); b=imm(T[1]) if T[1].startswith("#") else self._regexpr(T[1])
            self._setflags_sub(a,b,self.is32(T[0])); return nxt
        if m=="cmn":
            a=self.rd(T[0]); b=imm(T[1]) if T[1].startswith("#") else self._regexpr(T[1])
            self._setflags_sub(a,(-b)&M64,self.is32(T[0])); return nxt
        if m in ("ccmp","ccmn"):
            # ccmp Rn, Rm/#imm, #nzcv, cond
            cc=T[3]; nz=imm(T[2])
            if self._cond(cc):
                a=self.rd(T[0]); b=imm(T[1]) if T[1].startswith("#") else self._regexpr(T[1])
                if m=="ccmn": b=(-b)&M64
                self._setflags_sub(a,b,self.is32(T[0]))
            else:
                self.n=(nz>>3)&1; self.z=(nz>>2)&1; self.c=(nz>>1)&1; self.vf=nz&1
            return nxt
        if m in ("and","orr","eor","ands","bic"):
            a=self.rd(T[1])
            b=imm(T[2]) if T[2].startswith("#") else self._regexpr(T[2])
            if m=="and" or m=="ands": r=a&b
            elif m=="orr": r=a|b
            elif m=="eor": r=a^b
            elif m=="bic": r=a&~b
            r&=M64; self.wr(T[0],r)
            if m=="ands":
                bits=32 if self.is32(T[0]) else 64; rr=r&((1<<bits)-1)
                self.z=1 if rr==0 else 0; self.n=(rr>>(bits-1))&1
            return nxt
        if m in ("lsl","lsr","asr","lslv","lsrv","asrv","rorv"):
            w=self.is32(T[0]); a=self.rd(T[1])
            sh=imm(T[2]) if T[2].startswith("#") else (self.rd(T[2])&(31 if w else 63))
            bits=32 if w else 64
            if m in ("lsl","lslv"): r=a<<sh
            elif m in ("lsr","lsrv"): r=a>>sh
            elif m in ("ror","rorv"): sh%=bits; r=((a>>sh)|(a<<(bits-sh)))&((1<<bits)-1)
            else: r=_sx(a,bits)>>sh
            self.wr(T[0],r&M64); return nxt
        if m=="bfi":
            # bfi Xd, Xn, #lsb, #width
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            mask=((1<<width)-1)
            d=self.rd(T[0]); d=(d & ~(mask<<lsb)) | ((src&mask)<<lsb)
            self.wr(T[0],d&M64); return nxt
        if m=="bfxil":
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            d=self.rd(T[0]); val=(src>>lsb)&((1<<width)-1)
            d=(d & ~((1<<width)-1)) | val; self.wr(T[0],d&M64); return nxt
        if m=="ubfx":
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            self.wr(T[0],(src>>lsb)&((1<<width)-1)); return nxt
        if m=="ubfiz":
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            self.wr(T[0],((src&((1<<width)-1))<<lsb)&M64); return nxt
        if m=="sbfiz":
            # sbfiz Xd, Xn, #lsb, #width : signed field (low width bits) sign-extended, <<lsb
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            bits=32 if self.is32(T[0]) else 64; rmask=(1<<bits)-1
            sval=_sx(src&((1<<width)-1),width)
            self.wr(T[0],(sval<<lsb)&rmask); return nxt
        if m=="sbfx":
            # sbfx Xd, Xn, #lsb, #width : extract width bits at lsb, sign-extend
            lsb=imm(T[2]); width=imm(T[3]); src=self.rd(T[1])
            bits=32 if self.is32(T[0]) else 64; rmask=(1<<bits)-1
            sval=_sx((src>>lsb)&((1<<width)-1),width)
            self.wr(T[0],sval&rmask); return nxt
        if m=="cls":
            bits=32 if self.is32(T[0]) else 64; v=self.rd(T[1])&((1<<bits)-1)
            msb=(v>>(bits-1))&1; cnt=0
            for i in range(bits-2,-1,-1):
                if ((v>>i)&1)==msb: cnt+=1
                else: break
            self.wr(T[0],cnt); return nxt
        if m in ("ldar","ldarb","ldarh","ldaxr","ldxr"):
            addr=self._memaddr(o[o.index("["):])
            if m in("ldarb",): v=self.m.r(addr,1)
            elif m in("ldarh",): v=self.m.r(addr,2)
            else: v=self.m.r(addr,4) if self.is32(T[0]) else self.m.r(addr,8)
            self.wr(T[0],v); return nxt
        if m in ("stlr","stlrb","stlrh"):
            addr=self._memaddr(o[o.index("["):])
            v=self.rd(T[0])
            if m=="stlrb": self.m.w(addr,v,1)
            elif m=="stlrh": self.m.w(addr,v,2)
            else: self.m.w(addr,v,4 if self.is32(T[0]) else 8)
            return nxt
        if m in ("stlxr","stxr"):   # store-exclusive: status reg T[0], value T[1], [T[2]]
            addr=self._memaddr(o[o.index("["):])
            v=self.rd(T[1]); self.m.w(addr,v,4 if self.is32(T[1]) else 8); self.wr(T[0],0); return nxt
        # ---- SIMD/FP load/store (q/d/s/h/b and v<N>) ----
        if m in ("ldr","ldur","str","stur","ldp","stp","ldnp","stnp") and self._vinfo(T[0]) is not None:
            vi0=self._vinfo(T[0]); addr=self._memaddr(o[o.index("["):])
            if m in ("ldr","ldur"):
                self.vset(vi0[0], self.m.rb(addr,vi0[1])); return nxt
            if m in ("str","stur"):
                self.m.wb(addr, self.vget(vi0[0],vi0[1])); return nxt
            vi1=self._vinfo(T[1])
            if m in ("ldp","ldnp"):
                self.vset(vi0[0], self.m.rb(addr,vi0[1])); self.vset(vi1[0], self.m.rb(addr+vi0[1],vi1[1])); return nxt
            if m in ("stp","stnp"):
                self.m.wb(addr, self.vget(vi0[0],vi0[1])); self.m.wb(addr+vi0[1], self.vget(vi1[0],vi1[1])); return nxt
        # ---- SIMD moves / immediates ----
        if m in ("movi","mvni") and self._vinfo(T[0]) is not None:
            arr=T[0].split(".")[1].strip().lower() if "." in T[0] else "2d"
            immt=T[1].strip(); base=int(immt.split(",")[0].strip()[1:],0)
            sh=0
            if "," in immt and ("lsl" in immt or "msl" in immt): sh=int(immt.split("#")[-1],0)
            val=base<<sh
            if arr in ("16b","8b"): elem,eb=val&0xff,1
            elif arr in ("8h","4h"): elem,eb=val&0xffff,2
            elif arr in ("4s","2s"): elem,eb=val&0xffffffff,4
            else:   # 2d / 1d : 8-bit abcdefgh -> per-byte expand; for #0 just zero
                eb=8
                if base==0: elem=0
                else:
                    e=0
                    for bit in range(8):
                        if (base>>bit)&1: e|=0xff<<(8*bit)
                    elem=e
            buf=bytearray(16)
            for off in range(0,16,eb): buf[off:off+eb]=elem.to_bytes(eb,"little")
            if m=="mvni": buf=bytearray((~b)&0xff for b in buf)
            self.v[self._vinfo(T[0])[0]]=buf; return nxt
        if m in ("mov","orr") and self._vinfo(T[0]) is not None and self._vinfo(T[1] if len(T)>1 else "") is not None:
            self.v[self._vinfo(T[0])[0]]=bytearray(self.v[self._vinfo(T[1])[0]]); return nxt
        if m in ("ldr","ldrb","ldrh","ldrsw","ldur","ldurb","ldurh","ldrsh","ldrsb","ldursh","ldursb","ldursw"):
            addr=self._memaddr(o[o.index("["):])
            if m in("ldrb","ldurb"): v=self.m.r(addr,1)
            elif m in("ldrh","ldurh"): v=self.m.r(addr,2)
            elif m in("ldrsw","ldursw"): v=_sx(self.m.r(addr,4),32)&M64
            elif m in("ldrsh","ldursh"): v=_sx(self.m.r(addr,2),16)&(0xffffffff if self.is32(T[0]) else M64)
            elif m in("ldrsb","ldursb"): v=_sx(self.m.r(addr,1),8)&(0xffffffff if self.is32(T[0]) else M64)
            else: v=self.m.r(addr,4) if self.is32(T[0]) else self.m.r(addr,8)
            self.wr(T[0],v); return nxt
        if m in ("str","strb","strh","stur","sturb","sturh"):
            addr=self._memaddr(o[o.index("["):])
            v=self.rd(T[0])
            if m in("strb","sturb"): self.m.w(addr,v,1)
            elif m in("strh","sturh"): self.m.w(addr,v,2)
            else: self.m.w(addr,v,4 if self.is32(T[0]) else 8)
            return nxt
        if m=="stp":
            addr=self._memaddr(o[o.index("["):])
            sz=4 if self.is32(T[0]) else 8
            self.m.w(addr,self.rd(T[0]),sz); self.m.w(addr+sz,self.rd(T[1]),sz); return nxt
        if m=="ldp":
            addr=self._memaddr(o[o.index("["):])
            sz=4 if self.is32(T[0]) else 8
            self.wr(T[0],self.m.r(addr,sz)); self.wr(T[1],self.m.r(addr+sz,sz)); return nxt
        if m=="b": return int(T[0][1:],0)
        if m.startswith("b."):
            cc=m[2:];
            if self._cond(cc): return int(T[0][1:],0)
            return nxt
        if m=="cbz":
            if self.rd(T[0])==0: return int(T[1][1:],0)
            return nxt
        if m=="cbnz":
            if self.rd(T[0])!=0: return int(T[1][1:],0)
            return nxt
        if m=="tbz":
            if (self.rd(T[0])>>imm(T[1]))&1==0: return int(T[2][1:],0)
            return nxt
        if m=="tbnz":
            if (self.rd(T[0])>>imm(T[1]))&1==1: return int(T[2][1:],0)
            return nxt
        if m=="bl":
            self.lr=nxt; return int(T[0][1:],0)
        if m=="blr":
            self.lr=nxt; return self.rd(T[0])
        if m=="br": return self.rd(T[0])
        if m=="ret": return self.lr
        if m in("csel","csinc","csinv","csneg","cset","csetm"):
            if m in("cset","csetm"):
                cc=T[1]; val=1 if self._cond(cc) else 0
                if m=="csetm" and val: val=M64
                self.wr(T[0],val); return nxt
            cc=T[3]; a=self.rd(T[1]); b=self.rd(T[2])
            if self._cond(cc): self.wr(T[0],a)
            else:
                if m=="csinc": self.wr(T[0],(b+1)&M64)
                elif m=="csinv": self.wr(T[0],(~b)&M64)
                elif m=="csneg": self.wr(T[0],(-b)&M64)
                else: self.wr(T[0],b)
            return nxt
        if m in("cinc","cinv","cneg"):
            cc=T[2]; n=self.rd(T[1]); hit=self._cond(cc)
            if m=="cinc": r=(n+1)&M64 if hit else n
            elif m=="cinv": r=(~n)&M64 if hit else n
            else: r=(-n)&M64 if hit else n
            self.wr(T[0],r); return nxt
        if m=="mrs":
            self.wr(T[0], 0); return nxt          # tpidr_el0 -> 0 (TLS base); canary reads land in page 0
        if m=="msr": return nxt
        if m=="nop": return nxt
        if m in("uxtb","uxth","uxtw","sxtb","sxth","sxtw"):
            v=self.rd(T[1])
            if m=="uxtb": v&=0xff
            elif m=="uxth": v&=0xffff
            elif m=="uxtw": v&=0xffffffff
            elif m=="sxtb": v=_sx(v&0xff,8)&M64
            elif m=="sxth": v=_sx(v&0xffff,16)&M64
            elif m=="sxtw": v=_sx(v&0xffffffff,32)&M64
            self.wr(T[0],v); return nxt
        if m=="madd":
            self.wr(T[0], (self.rd(T[1])*self.rd(T[2])+self.rd(T[3]))&M64); return nxt
        if m=="msub":
            self.wr(T[0], (self.rd(T[3])-self.rd(T[1])*self.rd(T[2]))&M64); return nxt
        if m=="mul":
            self.wr(T[0], (self.rd(T[1])*self.rd(T[2]))&M64); return nxt
        if m in ("umull","umaddl","umsubl"):
            a=self.rd(T[1])&0xffffffff; b=self.rd(T[2])&0xffffffff; p=a*b
            if m=="umaddl": p=self.rd(T[3])+p
            elif m=="umsubl": p=self.rd(T[3])-p
            self.wr(T[0],p&M64); return nxt
        if m in ("smull","smaddl","smsubl"):
            a=_sx(self.rd(T[1])&0xffffffff,32); b=_sx(self.rd(T[2])&0xffffffff,32); p=a*b
            if m=="smaddl": p=_sx(self.rd(T[3]),64)+p
            elif m=="smsubl": p=_sx(self.rd(T[3]),64)-p
            self.wr(T[0],p&M64); return nxt
        if m=="umulh":
            self.wr(T[0], ((self.rd(T[1])*self.rd(T[2]))>>64)&M64); return nxt
        if m=="smulh":
            self.wr(T[0], ((_sx(self.rd(T[1]),64)*_sx(self.rd(T[2]),64))>>64)&M64); return nxt
        if m=="mneg":
            self.wr(T[0], (-self.rd(T[1]))&M64); return nxt
        if m=="tst":
            a=self.rd(T[0]); b=imm(T[1]) if T[1].startswith("#") else self._regexpr(T[1])
            r=a&b; bits=32 if self.is32(T[0]) else 64; r&=(1<<bits)-1
            self.z=1 if r==0 else 0; self.n=(r>>(bits-1))&1; return nxt
        if m=="mvn":
            self.wr(T[0], (~self._regexpr(T[1]))&M64); return nxt
        if m in ("neg","negs"):
            b=self._regexpr(T[1]); bits=32 if self.is32(T[0]) else 64; mask=(1<<bits)-1
            res=(-b)&mask; self.wr(T[0],res&M64)
            if m=="negs":
                self.z=1 if (res&mask)==0 else 0; self.n=(res>>(bits-1))&1
                self.c=1 if (0>=(b&mask)) and (b&mask)==0 else (1 if (b&mask)==0 else 0)
            return nxt
        if m in("rev","rev16","rev32","rev64"):
            v=self.rd(T[1]); w=self.is32(T[0]); nb=4 if w else 8
            by=v.to_bytes(8,"little")[:nb]
            if m=="rev" or (m=="rev64" and not w) or (m=="rev32" and w):
                r=int.from_bytes(by[::-1],"little")
            elif m=="rev16":
                r=0
                for i in range(0,nb,2): r|=int.from_bytes(by[i:i+2][::-1],"little")<<(8*i)
            elif m=="rev32":
                r=0
                for i in range(0,nb,4): r|=int.from_bytes(by[i:i+4][::-1],"little")<<(8*i)
            else: r=int.from_bytes(by[::-1],"little")
            self.wr(T[0],r&M64); return nxt
        if m=="clz":
            v=self.rd(T[1]); bits=32 if self.is32(T[0]) else 64; r=bits
            for i in range(bits-1,-1,-1):
                if (v>>i)&1: r=bits-1-i; break
            self.wr(T[0],r); return nxt
        if m=="rbit":
            v=self.rd(T[1]); bits=32 if self.is32(T[0]) else 64; r=0
            for i in range(bits):
                if (v>>i)&1: r|=1<<(bits-1-i)
            self.wr(T[0],r); return nxt
        if m=="extr":
            lsb=imm(T[3]); hi=self.rd(T[1]); lo=self.rd(T[2]); bits=32 if self.is32(T[0]) else 64
            cat=(hi<<bits)|lo; self.wr(T[0],(cat>>lsb)&((1<<bits)-1)); return nxt
        if m=="ror":
            v=self.rd(T[1]); sh=imm(T[2]) if T[2].startswith("#") else (self.rd(T[2])&63); bits=32 if self.is32(T[0]) else 64
            sh%=bits; self.wr(T[0],((v>>sh)|(v<<(bits-sh)))&((1<<bits)-1)); return nxt
        if m in("udiv","sdiv"):
            a=self.rd(T[1]);b=self.rd(T[2])
            if b==0: r=0
            elif m=="udiv": r=a//b
            else:
                bits=32 if self.is32(T[0]) else 64; sa=_sx(a,bits);sb=_sx(b,bits); q=abs(sa)//abs(sb); r=(q if (sa<0)==(sb<0) else -q)&M64
            self.wr(T[0],r&M64); return nxt
        if m=="dmb" or m=="dsb" or m=="isb" or m=="prfm" or m=="hint" or m=="sevl" or m=="yield": return nxt
        raise Exception("unhandled opcode '%s' @0x%x : %s"%(m,pc,o))
    def _cond(self,cc):
        n,z,c,v=self.n,self.z,self.c,self.vf
        return {"eq":z==1,"ne":z==0,"cs":c==1,"hs":c==1,"cc":c==0,"lo":c==0,
                "mi":n==1,"pl":n==0,"vs":v==1,"vc":v==0,"hi":(c==1 and z==0),
                "ls":(c==0 or z==1),"ge":n==v,"lt":n!=v,"gt":(z==0 and n==v),
                "le":(z==1 or n!=v),"al":True,"nv":True}[cc]
