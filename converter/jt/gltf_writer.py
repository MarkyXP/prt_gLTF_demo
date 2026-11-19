"""glTF 2.0 / GLB writer for decoded meshes.

Inputs:
  * The mesh collection produced by the extractor (list of dicts shaped
    {'stream': name, 't': decode_trimesh dict, ...}).

Outputs:
  * ``.gltf`` + ``.bin`` files and, optionally, one self-contained
    ``.glb``.

Positions in meters (vendor units), per-corner normals, indices as
triangle list; one primitive per decoded mesh. A scene-root matrix converts
NX/JT Z-up coordinates to glTF Y-up coordinates. Asserts buffer invariants
while assembling accessors and buffer views.
"""

import os
import struct

from .gltf_materials import build_material as _build_material

def write_gltf(meshes, out_stem, glb=False, part_name=None,
               component_by_mesh=None, instance_parent_by_mesh=None):
    """Write glTF 2.0 (.gltf + .bin) and optionally single .glb.

    meshes = list of {'stream': name, 't': decode_trimesh dict}.
    Positions in meters (vendor units), per-corner normals, indices as
    triangle list; one primitive per decoded mesh. Asserts buffer invariants.

    > example
    > write_gltf(collection, base, glb=True) -> writes base.glb/base.gltf/
    >   base.bin and returns the (buffers-by-uri) JSON document dict
    """
    import json as _json

    bufs = bytearray()
    nodes, meshes_out, records = [], [], []
    for i, m in enumerate(meshes):
        t = m['t']
        pts, tris = t['pts'], t['tris']
        if not pts or not tris:
            continue
        cn = t.get('cornerNormals')
        cc = t.get('cornerColors')
        uvs = t.get('uvs')
        full_material = m.get('material')
        override_vertex_colors = bool(
            full_material and full_material.get('override_vertex_colors'))
        corner_normals = (
            cn if cn and len(cn) == 3 * len(tris) else None
        )
        corner_colors = (
            cc if cc and len(cc) == 3 * len(tris)
            and not override_vertex_colors else None
        )
        if corner_normals or corner_colors:
            render_pts = []
            render_normals = [] if corner_normals else None
            render_colors = [] if corner_colors else None
            render_uvs = [] if uvs else None
            render_tris = []
            for ti, tri in enumerate(tris):
                base = len(render_pts)
                for k, vi in enumerate(tri):
                    render_pts.append(pts[vi])
                    if render_normals is not None:
                        render_normals.append(corner_normals[3 * ti + k])
                    if render_colors is not None:
                        render_colors.append(corner_colors[3 * ti + k])
                    if render_uvs is not None:
                        render_uvs.append(uvs[vi])
                render_tris.append((base, base + 1, base + 2))
        else:
            render_pts = pts
            render_normals = None
            render_colors = None
            render_uvs = uvs
            render_tris = tris
        while len(bufs) % 4:
            bufs += b'\x00'
        prim = {'attributes': {}}
        pos0 = len(bufs)
        prim['_pos0'] = pos0
        prim['_posCount'] = len(render_pts)
        for pt in render_pts:
            for x in pt:
                bufs += struct.pack('<f', x)
        assert len(bufs) - pos0 == 12 * len(render_pts)
        prim['attributes']['POSITION'] = None
        if render_normals is not None:
            n0 = len(bufs)
            for n in render_normals:
                for x in n:
                    bufs += struct.pack('<f', x)
            prim['_normOff'] = (n0, len(render_normals))
        if render_uvs is not None:
            uv0 = len(bufs)
            for uv in render_uvs:
                bufs += struct.pack('<ff', float(uv[0]), float(uv[1]))
            prim['_uvOff'] = (uv0, len(render_uvs))
        if render_colors is not None:
            c0 = len(bufs)
            translucent = False
            for color in render_colors:
                values = tuple(float(value) for value in color)
                if len(values) == 3:
                    values += (1.0,)
                if len(values) != 4:
                    raise ValueError(
                        'vertex color must have three or four components')
                translucent = translucent or values[3] < 1.0
                bufs += struct.pack('<ffff', *values)
            prim['_colorOff'] = (c0, len(render_colors))
            prim['_vertexColors'] = True
            prim['_vertexBlend'] = translucent
        if t.get('image'):
            prim['_image'] = (t.get('imageName') or ('image-%d' % i),
                              t['image'])
        i0 = len(bufs)
        need32 = bool(render_tris) and max(max(T) for T in render_tris) > 65535
        for T in render_tris:
            bufs += struct.pack('<III' if need32 else '<HHH', *T)
        prim['_idx'] = (i0, 3 * len(render_tris))
        prim['_idxComp'] = 5125 if need32 else 5123
        prim['_idxW'] = 4 if need32 else 2
        prim['_color'] = m.get('color')
        prim['_fullMaterial'] = m.get('material')
        prim['_doubleSided'] = bool(m.get('doubleSided'))
        proxy_role = m.get('proxyRole')
        instances = m.get('instances') or []
        component = (
            (component_by_mesh or {}).get(id(m)) or m.get('component'))
        node_name = part_name or ('part-%d' % i)
        today = len(instances)
        if instances:
            node_name = instances[0].get('name') or node_name
        else:
            node = {'name': node_name, 'mesh': len(meshes_out)}
            if component:
                node['extras'] = {'component': component}
            nodes.append(node)
        extras = {'sourceStream': m.get('stream')}
        if component:
            extras['component'] = component
        if proxy_role:
            extras['proxyRole'] = proxy_role
        if instances:
            extras['instanceCount'] = today
        meshes_out.append({'name': 'mesh-%d' % i,
                           'primitives': [prim],
                           'extras': extras})
        for instance in instances:
            node = {'name': instance.get('name') or node_name,
                    'mesh': len(meshes_out) - 1,
                    'matrix': instance['matrix']}
            if component:
                extras = {'component': component}
                parent = (instance_parent_by_mesh or {}).get(id(m))
                if parent:
                    extras['parentComponent'] = parent
                occurrence_index = instance.get('occurrenceIndex')
                if occurrence_index is not None:
                    extras['instanceIndex'] = int(occurrence_index)
                else:
                    instance_name = instance.get('name') or ''
                    if '#' in instance_name:
                        try:
                            extras['instanceIndex'] = int(
                                instance_name.rsplit('#', 1)[1])
                        except ValueError:
                            pass
                node['extras'] = extras
            nodes.append(node)
        records.append((meshes_out[-1], render_pts, render_tris))

    views, accessors, byteEnd = [], [], 0
    for mo, pts, tris in records:
        prim = mo['primitives'][0]
        vpos = len(views)
        views.append({'buffer': 0, 'byteOffset': prim['_pos0'],
                      'byteLength': 12 * len(pts), 'target': 34962})
        accessors.append({'bufferView': vpos, 'componentType': 5126,
                          'count': len(pts), 'type': 'VEC3',
                          'min': [min(p[k] for p in pts) for k in range(3)],
                          'max': [max(p[k] for p in pts) for k in range(3)]})
        prim['attributes']['POSITION'] = len(accessors) - 1
        byteEnd = prim['_pos0'] + 12 * len(pts)
        if '_normOff' in prim:
            n0, nc = prim.pop('_normOff')
            vn = len(views)
            views.append({'buffer': 0, 'byteOffset': n0,
                          'byteLength': 12 * nc, 'target': 34962})
            accessors.append({'bufferView': vn, 'componentType': 5126,
                              'count': nc, 'type': 'VEC3'})
            prim['attributes']['NORMAL'] = len(accessors) - 1
            assert n0 == byteEnd
            byteEnd += 12 * nc
        if '_uvOff' in prim:
            u0, uc = prim.pop('_uvOff')
            vu = len(views)
            views.append({'buffer': 0, 'byteOffset': u0,
                          'byteLength': 8 * uc, 'target': 34962})
            accessors.append({'bufferView': vu, 'componentType': 5126,
                              'count': uc, 'type': 'VEC2'})
            prim['attributes']['TEXCOORD_0'] = len(accessors) - 1
            assert u0 == byteEnd
            byteEnd += 8 * uc
        if '_colorOff' in prim:
            c0, ccnt = prim.pop('_colorOff')
            vc = len(views)
            views.append({'buffer': 0, 'byteOffset': c0,
                          'byteLength': 16 * ccnt, 'target': 34962})
            accessors.append({'bufferView': vc, 'componentType': 5126,
                              'count': ccnt, 'type': 'VEC4'})
            prim['attributes']['COLOR_0'] = len(accessors) - 1
            assert c0 == byteEnd
            byteEnd += 16 * ccnt
        i0, ic = prim.pop('_idx')
        icomp = prim.pop('_idxComp', 5123)
        iw = prim.pop('_idxW', 2)
        vi = len(views)
        views.append({'buffer': 0, 'byteOffset': i0,
                      'byteLength': iw * ic, 'target': 34963})
        accessors.append({'bufferView': vi, 'componentType': icomp,
                          'count': ic, 'type': 'SCALAR'})
        prim['indices'] = len(accessors) - 1
        prim['mode'] = 4
        prim['material'] = prim.pop('_mat', 0)
        assert i0 == byteEnd
        byteEnd += iw * ic
    while len(bufs) % 4:
        bufs += b'\x00'
    default_mat = {'name': 'default',
                   'pbrMetallicRoughness': {'baseColorFactor': [0.8, 0.8, 0.8, 1.0],
                                            'metallicFactor': 0.1,
                                            'roughnessFactor': 0.6}}
    materials = [default_mat]
    images, textures, samplers = [], [], []
    image_index = {}
    mat_index = {}
    for mo in meshes_out:
        prim = mo['primitives'][0]
        col = prim.pop('_color', None)
        full_material = prim.pop('_fullMaterial', None)
        double_sided = prim.pop('_doubleSided', False)
        vertex_colors = prim.pop('_vertexColors', False)
        vertex_blend = prim.pop('_vertexBlend', False)
        image = prim.pop('_image', None)
        if image is not None:
            name, payload = image
            key = (name, len(payload))
            if key not in image_index:
                import base64
                uri = 'data:image/png;base64,' + base64.b64encode(payload).decode('ascii')
                images.append({'name': name, 'uri': uri})
                samplers.append({'magFilter': 9729, 'minFilter': 9729,
                                 'wrapS': 33071, 'wrapT': 33071})
                textures.append({'sampler': len(samplers) - 1,
                                 'source': len(images) - 1})
                image_index[key] = len(textures) - 1
            texture = image_index[key]
            material = {'name': 'decal-%d' % texture,
                        'pbrMetallicRoughness': {
                            'baseColorTexture': {'index': texture},
                            'metallicFactor': 0.0,
                            'roughnessFactor': 0.8,
                        },
                        'alphaMode': 'BLEND',
                        'doubleSided': True}
            materials.append(material)
            prim['material'] = len(materials) - 1
            continue
        if full_material is not None:
            material_key, material = _build_material(
                full_material, col, double_sided, vertex_colors,
                vertex_blend, len(materials))
            if material_key not in mat_index:
                mat_index[material_key] = len(materials)
                materials.append(material)
            prim['material'] = mat_index[material_key]
            continue
        if vertex_colors:
            material_key = ("vertex-colors", double_sided, vertex_blend)
            if material_key not in mat_index:
                mat_index[material_key] = len(materials)
                material = {'name': 'vertex-colors-%d' % len(materials),
                            'pbrMetallicRoughness': {
                                'baseColorFactor': [1.0, 1.0, 1.0, 1.0],
                                'metallicFactor': 0.1,
                                'roughnessFactor': 0.6}}
                if vertex_blend:
                    material['alphaMode'] = 'BLEND'
                if double_sided:
                    material['doubleSided'] = True
                materials.append(material)
            prim['material'] = mat_index[material_key]
            continue
        if not col:
            if double_sided:
                key = ("default", True)
                if key not in mat_index:
                    mat_index[key] = len(materials)
                    material = dict(default_mat)
                    material['name'] = 'default-double-sided'
                    material['doubleSided'] = True
                    materials.append(material)
                prim['material'] = mat_index[key]
            else:
                prim['material'] = 0
            continue
        key = tuple(round(float(x), 6) for x in col)
        if len(key) == 3:
            key += (1.0,)
        if len(key) != 4:
            raise ValueError('mesh color must have three or four components')
        material_key = (key, double_sided)
        if material_key not in mat_index:
            mat_index[material_key] = len(materials)
            material = {'name': 'mat-%d' % len(materials),
                        'pbrMetallicRoughness': {
                            'baseColorFactor': [float(x) for x in key],
                            'metallicFactor': 0.1,
                            'roughnessFactor': 0.6}}
            if key[3] < 1.0:
                material['alphaMode'] = 'BLEND'
                material['doubleSided'] = True
            elif double_sided:
                material['doubleSided'] = True
            materials.append(material)
        prim['material'] = mat_index[material_key]
    scene_roots = list(range(len(nodes)))
    nodes.append({
        'name': 'NX Z-up to glTF Y-up',
        'children': scene_roots,
        'matrix': [
            1, 0, 0, 0,
            0, 0, -1, 0,
            0, 1, 0, 0,
            0, 0, 0, 1,
        ],
    })
    doc = {
        'asset': {'version': '2.0',
                  'generator': 'prt_jt.py (NX .prt / embedded JT v9.4 decode)'},
        'scene': 0,
        'scenes': [{'nodes': [len(nodes) - 1]}],
        'nodes': nodes,
        'meshes': meshes_out,
        'materials': materials,
        'accessors': accessors,
        'buffers': [{'byteLength': len(bufs)}],
        'bufferViews': views,
    }
    if images:
        doc['images'] = images
        doc['textures'] = textures
        doc['samplers'] = samplers
    json_text = _json.dumps(doc)
    if glb:

        def _pad4(b, fill=b'\x00'):
            while len(b) % 4:
                b += fill
            return b
        json_b = _pad4(json_text.encode('utf8'), b' ')
        binb = _pad4(bytes(bufs))
        total = 12 + 8 + len(json_b) + 8 + len(binb)
        out = b'glTF' + struct.pack('<II', 2, total)
        out += struct.pack('<II', len(json_b), 0x4E4F534A) + json_b
        out += struct.pack('<II', len(binb), 0x004E4942) + binb
        assert len(out) == total
        open(out_stem + '.glb', 'wb').write(out)
    jdoc = dict(doc)
    jdoc['buffers'] = [{'uri': os.path.basename(out_stem) + '.bin',
                        'byteLength': len(bufs)}]
    open(out_stem + '.gltf', 'w').write(_json.dumps(jdoc, indent=1))
    open(out_stem + '.bin', 'wb').write(bytes(bufs))
    _json.loads(_json.dumps(jdoc))
    return jdoc

def _only_buffer_views(meshes):
    """Build the canonical bufferViews: per mesh [pos, [norm], idx]."""
    views = []
    goff = 0
    for m in meshes:
        t = m['t']
        views.append({'buffer': 0, 'byteOffset': goff,
                      'byteLength': 12 * t['npts'],
                      'target': 34962})
        goff += 12 * t['npts']
        if t.get('cornerNormals'):
            views.append({'buffer': 0, 'byteOffset': goff,
                          'byteLength': 12 * 3 * t['ntris'],
                          'target': 34962})
            goff += 12 * 3 * t['ntris']
        views.append({'buffer': 0, 'byteOffset': goff,
                      'byteLength': 6 * 3 * t['ntris'],
                      'target': 34963})
        goff += 6 * 3 * t['ntris']
    return views

def _gltf_json_ok(base):
    """True when the companion .gltf/.bin pair parses with matching URIs."""
    import json as _json
    try:
        d = _json.load(open(base + '.gltf'))
        uris = [b.get('uri') for b in d.get('buffers', [])]
        return all(u == os.path.basename(base) + '.bin' for u in uris) and \
            os.path.getsize(base + '.bin') > 0
    except Exception as ex:
        print('json check failed:', ex)
        return False
