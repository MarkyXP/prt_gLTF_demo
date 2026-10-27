# -*- coding: utf-8 -*-
# emu4.py - correct x86-64 emu for FUN_1800617f0 (CadEx v9 bitlength worker)
# Ops present: MOV MOVSXD LEA ADD SUB XOR OR INC DEC CDQ CMP TEST
#              SHL SHR SAR Jcc(JNZ JZ JL JGE JLE JG JS JMP) CMOVL CMOVZ
#              PUSH POP CALL(const) RET NOP
import os, re, struct

_HERE = os.path.dirname(os.path.abspath(__file__))
_LISTING = os.path.join(_HERE, "listing_1800617f0.txt")

M32 = 0xFFFFFFFF
M64 = (1 << 64) - 1
R64N = ["RAX","RCX","RDX","RBX","RSP","RBP","RSI","RDI",
        "R8","R9","R10","R11","R12","R13","R14","R15"]
R32MAP = {"EAX":"RAX","ECX":"RCX","EDX":"RDX","EBX":"RBX","EBP":"RBP","ESI":"RSI",
          "EDI":"RDI","R8D":"R8","R9D":"R9","R10D":"R10","R11D":"R11","R12D":"R12",
          "R13D":"R13","R14D":"R14","R15D":"R15",
          "RAX":"RAX","RCX":"RCX","RDX":"RDX","RBX":"RBX","RBP":"RBP","RSI":"RSI",
          "RDI":"RDI","RSP":"RSP"}

def sx32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v

class Emu:
    def __init__(self, listing):
        self.code = []
        for line in listing.splitlines():
            line = line.strip()
            if not line or line.startswith("//"): continue
            m = re.match(r"^(0x[0-9a-fA-F]+):\s*(.*)$", line)
            self.code.append((int(m.group(1),16), m.group(2).strip()))
        self.by_addr = {a:i for i,(a,t) in enumerate(self.code)}
        mem, r = {}, {}
        self.set_r = lambda n,v: mem
        self.mem = mem
        self.r = r
        for n in R64N: r[n] = 0
        self.ip = 0
        self.zf = self.sf = self.of = self.cf = 0
        self.steps = 0
        self.done = False

    # memory
    def rd(self, a, size):
        a &= M64
        v = 0
        for k in range(size):
            v |= self.mem.get((a+k) & M64, 0) << (8*k)
        return v

    def wr(self, a, v, size):
        a &= M64
        v &= (1 << (8*size)) - 1
        for k in range(size):
            self.mem[(a+k) & M64] = (v >> (8*k)) & 0xFF

    # address expression evaluator
    def memaddr(self, expr):
        e = expr.replace(" ", "")
        if e.startswith("[") and e.endswith("]"): e = e[1:-1]
        # tokenize signed components: handle form REG + REG*SCALE + disp  or plain
        total = 0
        sgn = 1
        for m in re.finditer(r"([+-]?)([^+-]+)", e):
            sign = -1 if m.group(1) == "-" else 1
            tok = m.group(2)
            if "*" in tok:
                a, b = tok.split("*")
                if a.startswith("0x"): a, b = b, a
                if b.startswith("0x"): sc = int(b, 16)
                else: sc = int(b)
                reg = a if not a.startswith("0x") else None
                total += sign * ((self.r[reg] & M32) * sc)
            elif re.match(r"^(R(A|B|C|D)X|RSP|RBP|RSI|RDI|R\d+)$", tok):
                total += sign * (self.r[tok] & M32)
            else:
                total += sign * int(tok, 16) if tok.lower().startswith("0x") else sign*int(tok)
        return total & M64

    def getr(self, name):
        name = name.strip()
        full = R32MAP.get(name)
        if full: return self.r[full] & M32
        if name in self.r: return self.r[name] & M64
        if name in ("AL",): return self.r["RAX"] & 0xFF
        if name in ("CL",): return self.r["RCX"] & 0xFF
        if name in ("BL",): return self.r["RBX"] & 0xFF
        if name in ("DL",): return self.r["RDX"] & 0xFF
        m = re.match(r"(?:(qword|dword) ptr )\[(.+)\]$", name)
        if m:
            a = self.memaddr(m.group(2))
            return self.rd(a, 8 if m.group(1) == "qword" else 4)
        if name.startswith(("0x", "-0x")): return int(name,16) & M64
        if re.match(r"^-?\d+$", name): return int(name, 16) if name.startswith("-0x") else int(name)
        raise ValueError("r? " + name)

    def getr_s32(self, name):
        return sx32(self.getr(name))

    def setr(self, name, v):
        name = name.strip()
        if name in self.r: self.r[name] = v & M64; return
        full = R32MAP.get(name)
        if full:
            if full in R64N and name != full:
                self.r[full] = v & M32          # x86-64: 32-bit write zero-extends
            else:
                self.r[full] = v & M64
            return
        if full is None and name in self.r: self.r[name] = v & M64; return
        if name == "AL": self.r["RAX"] = (self.r["RAX"] & ~0xFF) | (v & 0xFF); return
        m = re.match(r"(?:(qword|dword) ptr )?\[(.+)\]$", name)
        if m:
            a = self.memaddr(m.group(2))
            self.wr(a, v, 8 if m.group(1) == "qword" else 4)
            return
        raise ValueError("w? " + name)

    def flag_sub(self, x, y, keepcf=False):
        x &= M32; y &= M32
        r = (x - y) & M32
        xr, yr, rr = x, y, r
        self.zf = 1 if r == 0 else 0
        self.sf = (r >> 31) & 1
        self.of = 1 if (((xr ^ yr) & (xr ^ rr)) >> 31) & 1 else 0
        self.cf = 1 if (x & M32) < (y & M32) else 0
        return r

    def flag_add(self, x, y):
        x &= M32; y &= M32
        r = (x + y) & M32
        self.zf = 1 if r == 0 else 0
        self.sf = (r >> 31) & 1
        self.of = 1 if ((((~x) & M32) ^ y) & ((~r) & M32) & (x ^ r)) >> 31 else 0
        self.of = 1 if (((x ^ r) & (y ^ r)) >> 31) & 1 else 0
        self.cf = 1 if r < 0 else 0
        self.cf = 1 if (x & M32) + (y & M32) > M32 else 0
        return r

    def flag_logic(self, r):
        self.zf = 1 if (r & M32) == 0 else 0
        self.sf = (r >> 31) & 1
        self.of = self.cf = 0
        return r

    def shift(self, dst, cnt, kind):
        n = cnt & 31
        if n == 0:
            return self.getr(dst)
        v = self.getr(dst)
        if kind == "SHL":
            self.cf = (v >> (32 - n)) & 1
            r = (v << n) & M32
            self.of = 1 if ((v ^ r) >> 31) & 1 else 0
        elif kind == "SHR":
            r = (v & M32) >> n
            self.cf = (v >> (n-1)) & 1
            self.of = (v >> 31) & 1
        else:  # SAR
            r = (sx32(v) >> n) & M32
            self.cf = (v >> (n-1)) & 1
            self.of = 0
        self.flag_logic(r)
        return r

    def cond(self, cc):
        if cc in ("Z","E"): return self.zf == 1
        if cc in ("NZ","NE"): return self.zf == 0
        if cc == "L": return self.sf != self.of
        if cc in ("GE","NL"): return self.sf == self.of
        if cc == "LE": return self.zf == 1 or (self.sf != self.of)
        if cc == "G": return self.zf == 0 and (self.sf == self.of)
        if cc == "S": return self.sf == 1
        if cc in ("NS",): return self.sf == 0
        if cc == "B": return self.cf == 1
        if cc in ("AE",): return self.cf == 0
        raise ValueError(cc)

    def step(self):
        addr, txt = self.code[self.ip]
        self.ip += 1
        t = txt.split(";")[0].strip()
        if t.startswith("NOP") or not t: return
        mm = re.match(r"^(\S+)\s*(.*)$", t)
        op, rest = mm.group(1).upper(), mm.group(2).strip()
        if op in ("JNZ","JNE"):
            if self.cond("NZ"): self.ip = self.by_addr[int(rest,16)]
        elif op in ("JZ","JE"):
            if self.cond("Z"): self.ip = self.by_addr[int(rest,16)]
        elif op in ("JL",):
            if self.cond("L"): self.ip = self.by_addr[int(rest,16)]
        elif op in ("JGE","JNL"):
            if self.cond("GE"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JLE":
            if self.cond("LE"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JG":
            if self.cond("G"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JS":
            if self.cond("S"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JNS":
            if self.cond("NS"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JB":
            if self.cond("B"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JAE":
            if self.cond("AE"): self.ip = self.by_addr[int(rest,16)]
        elif op == "JMP":
            self.ip = self.by_addr[int(rest,16)]
        elif op in ("CMOVNZ","CMOVNE"):
            if self.cond("NZ"): self.setr(*rest.split(","))
        elif op in ("CMOVZ","CMOVE"):
            if self.cond("Z"): self.setr(*rest.split(","))
        elif op == "CMOVL":
            if self.cond("L"):
                a, b = rest.split(","); self.setr(a, self.getr(b))
        elif op == "CMOVGE":
            if self.cond("GE"):
                a, b = rest.split(","); self.setr(a, self.getr(b))
        elif op == "CMOVS" and self.cond("S"): self.setr(*rest.split(","))
        elif op == "CMOVAE" and self.cond("AE"): self.setr(*rest.split(","))
        elif op == "TEST":
            a, b = rest.split(",")
            self.flag_logic(self.getr(a) & self.getr(b))
        elif op == "CMP":
            a, b = rest.split(",")
            self.flag_sub(self.getr(a), self.getr(b))
        elif op in ("MOV","MOVSXD"):
            a, b = rest.split(",", 1)
            v = self.getr(b)
            if op == "MOVSXD":
                if a in R32MAP and a != "EDX": v = sx32(v) & M32
                else: v = sx32(v) & M64
            self.setr(a, v)
        elif op == "LEA":
            a, b = rest.split(",", 1)
            is32 = a in ("EAX","ECX","EDX","EBX","EBP","ESI","EDI") or (a.startswith("R") and a.endswith("D"))
            self.setr(a, self.memaddr(b) & (M32 if is32 else M64))
        elif op == "ADD":
            a, b = rest.split(",")
            self.setr(a, self.flag_add(self.getr(a), self.getr(b)))
        elif op == "SUB":
            a, b = rest.split(",")
            self.setr(a, self.flag_sub(self.getr(a), self.getr(b)))
        elif op == "XOR":
            a, b = rest.split(",")
            self.setr(a, self.flag_logic(self.getr(a) ^ self.getr(b)))
        elif op == "OR":
            a, b = rest.split(",")
            self.setr(a, self.flag_logic(self.getr(a) | self.getr(b)))
        elif op == "SHL" or op == "SAL":
            a, b = rest.split(",")
            self.setr(a, self.shift(a, self.getr(b), "SHL"))
        elif op == "SHR":
            a, b = rest.split(",")
            self.setr(a, self.shift(a, self.getr(b), "SHR"))
        elif op == "SAR":
            a, b = rest.split(",")
            self.setr(a, self.shift(a, self.getr(b), "SAR"))
        elif op == "INC":
            self.setr(rest, self.flag_add(self.getr(rest), 1))
        elif op == "DEC":
            self.setr(rest, self.flag_sub(self.getr(rest), 1))
        elif op == "CDQ":
            self.r["RDX"] = M32 if self.r["RAX"] & 0x80000000 else 0
        elif op == "PUSH":
            self.r["RSP"] = (self.r["RSP"] - 8) & ~7
            self.wr(self.r["RSP"], self.getr(rest), 8)
        elif op == "POP":
            self.setr(rest, self.rd(self.r["RSP"], 8))
            self.r["RSP"] = (self.r["RSP"] + 8) & M64
        elif op == "CALL":
            tgt = int(rest, 16)
            self.do_call(tgt)
        elif op == "RET":
            self.done = True
        else:
            raise ValueError("op? " + t)

    def do_call(self, tgt):
        if tgt == 0x180049120:
            return  # vector resize: caller pre-allocated
        raise ValueError("unknown call %x at %x" % (tgt, self.code[self.ip-1][0]))

    def run(self, maxsteps=5_000_000):
        while not self.done and 0 <= self.ip < len(self.code) and self.steps < maxsteps:
            self.steps += 1
            self.step()
        return self.steps


# ---- worker driver ---------------------------------------------------------
def decode_v9_bitlength(nvalues, words, nbits, verbose=False):
    """Runs FUN_1800617f0 on one bitlength packet. Returns list of signed ints."""
    e = Emu(open(_LISTING, encoding='utf-8', errors='replace').read())
    ST   = 0x700000   # state struct (rcx)
    VEC  = 0x701000   # out vector object
    DATA = 0x702000   # out data storage
    WOBJ = 0x703000   # words object ([+0]=data ptr)
    WDATA= 0x704000
    E    = 0x710000   # entry rsp (home slots E+8..E+0x28)
    # words object
    e.wr(WOBJ, WDATA, 8)
    for i, w in enumerate(words):
        e.wr(WDATA + 4*i, w & M32, 4)
    e.wr(WDATA + 4*len(words) + 8, 0xAAAA, 8)  # end sentinel
    # out vector: [VEC] = DATA
    e.wr(VEC, DATA, 8)
    # entry-state args (shadow home slots)
    e.wr(E + 0x08, ST, 8)         # arg1 state ptr
    e.wr(E + 0x10, nvalues & M32, 4)  # arg2 nValues
    e.wr(E + 0x18, WOBJ, 8)       # arg3 words obj
    e.wr(E + 0x20, nbits & M32, 4)# arg4 nBits
    e.wr(E + 0x28, VEC, 8)        # arg5 out vector
    # state struct: [state+8] = &(E+0x20) i.e. ptr to a dword holding nBits
    e.wr(ST + 0x8, E + 0x20, 8)
    e.r["RSP"] = E
    e.r["RCX"] = ST
    e.r["RDX"] = nvalues & M32
    e.r["R8"]  = WOBJ
    e.r["R9"]  = nbits & M32
    e.run()
    out = []
    i = 0
    while True:
        cell = e.rd(DATA + 4*i, 4)
        if cell == 0 and i > 0 and e.rd(DATA + 4*(i+1), 4) in (0, 0xAAAA):
            # heuristic end: got enough? we just read nvalues entries typed by nvalues cap
            pass
        if i >= nvalues:
            break
        # no sentinel check; read exactly nvalues
        v = sx32(e.rd(DATA + 4*i, 4))
        out.append(v)
        i += 1
    if verbose:
        print("  steps", e.steps)
    return out


if __name__ == "__main__":
    d = open("lod0_obj.bin", "rb").read()
    cnt   = struct.unpack_from("<i", d, 20)[0]
    ctbits= struct.unpack_from("<i", d, 30)[0]
    words = struct.unpack_from("<5I", d, 35)
    print("FD0", cnt, ctbits, " ".join("%08x" % w for w in words))
    vals = decode_v9_bitlength(cnt, words, ctbits, verbose=True)
    print("FD0 syms:", vals)
