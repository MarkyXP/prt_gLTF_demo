"""Command-line probe for the JT decode toolchain.

Inputs:
  * optional argv[1] = .prt or raw JT path (default sample.jt beside the
    converter folder).

Outputs:
  * Stream table, decode summaries/warnings, LSG material diagnostics,
    and a GLB/gltf/bin export next to the input stem.
"""

import os
import sys
from .container import OLE_MAGIC, iter_jt_streams, open_prt
from .extractor import extract_meshes
from .gltf_writer import _gltf_json_ok, write_gltf

_CONVERTER_DIR = os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))

def _main(argv):
    if len(argv) > 1:
        path = argv[1]
    else:
        path = os.path.join(_CONVERTER_DIR, 'sample.jt')
    got_splm = open(path, 'rb').read(4) == b'SPLM'
    if open(path, 'rb').read(8) == OLE_MAGIC:
        print('OLE2/CFBF container (classic NX storage)')
    coll = []
    if got_splm:
        streams = open_prt(path)
        print('SPLM container: %d streams' % len(streams))
        for k, v in streams.items():
            print('  %-42s @%7d len %6d' % (k, v.start, v.length))
        for name, s in iter_jt_streams(streams):
            print('JT stream: %s (%d B)' % (name, s.length))
            for m in extract_meshes(s.data):
                print('  summary:', m['summary'])
                print('  warnings:', m['warnings'])
                la = m.get('lsgAttrs')
                if la is not None:
                    print('  LSG materials: %d' % len(la['materials']))
                    for mm in la['materials']:
                        print('    oid %s diffuse=%s quads=%d' % (
                            mm.get('oid'), mm.get('diffuse'), len(mm.get('quads', ()))))
                    print('  lateLoaded segment refs: %d' % len(la['lateLoaded']))
                    for ll in la['lateLoaded']:
                        print('    oid %s -> segType %d seg %s' % (
                            ll['oid'], ll['segType'], ll['segGuid']))
                    print('  paletteMap:', 'present (%d entries)' % len(la['paletteMap']['vector']) if la['paletteMap'] else 'absent')
                for t in m['meshes']:
                    print('   mesh: %d pts %d tris color=%s' % (t['npts'], t['ntris'],
                                                               t.get('color')))
                    coll.append({'stream': s.name, 't': t,
                                 'color': t.get('color')})
    else:
        for m in extract_meshes(open(path, 'rb').read()):
            for t in m['meshes']:
                coll.append({'stream': m['stream'], 't': t})
    if coll:
        stem = os.path.splitext(os.path.basename(path))[0]
        base = os.path.join(_CONVERTER_DIR, stem)
        write_gltf(coll, base, glb=True)
        print('wrote %s.glb  %s.gltf + %s.bin  (tris=%d pts=%d, json-ok=%s)' % (
            base, base, base, sum(len(o['t']['tris']) for o in coll),
            sum(len(o['t']['pts']) for o in coll), _gltf_json_ok(base)))
    return 0

if __name__ == '__main__':
    sys.exit(_main(sys.argv))
