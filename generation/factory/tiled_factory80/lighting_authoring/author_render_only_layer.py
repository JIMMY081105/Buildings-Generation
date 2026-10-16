"""OWN render-only input author. Default imports no PXR/SDK and writes nothing.

ROOT alone authorizes actual standalone MAIN CPU execution. All original files
remain readonly; only a fresh render layer and provenance receipts are exported.
"""
import argparse
import hashlib
import importlib
import inspect
import json
import math
import os
import re
import stat
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).absolute().parent
W = Path('/cephfs/shared/yixing/outputs/factory80_20261011')
MAIN = '/cephfs/shared/yixing/envs/main/bin/python'
CPU_LOCK = W/'work/gate_audit/CPU_HEAVY.lock'
CPU_ID = (55, 1100794017351)
OWN = '/World/Factory80RenderOnly'
OWNER = 'PROJECT_OWNED_PROCEDURAL_NO_THIRD_PARTY_INPUTS'
ASSET_NAMES = ['concrete', 'painted_steel', 'safety_yellow']
ROLES = ['albedo', 'roughness', 'normal', 'metallic']


from stat_hash_cache import StatHashCache
HASH_CACHE=StatHashCache([W/'work/P1_HASH_CACHE_20261015.json',W/'work/P5_R4_NEW_GEOMETRY_CHAIN_20261015_R3/original_subject/ACTUAL_STAT_HASH_CACHE.json'])
def ref(p):return HASH_CACHE.ref(Path(p).absolute())


def read(p):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise ValueError('Duplicate JSON key '+k)
            result[k] = v
        return result
    return json.loads(Path(p).read_bytes(), object_pairs_hook=pairs,
        parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite '+v)))


def check(r):
    if set(r) != {'path', 'bytes', 'sha256'} or type(r['bytes']) is not int or not re.fullmatch('[0-9a-f]{64}', r['sha256']):
        raise ValueError('Invalid full reference')
    if ref(r['path']) != r: raise ValueError('Bound source changed: '+r['path'])
    return r


def unique(rows):
    out = {}
    for r in rows:
        if r['path'] in out and out[r['path']] != r: raise ValueError('Conflicting same-path source refs')
        out[r['path']] = r
    return list(out.values())


def binding(a):
    idx = ref(a.source_index)
    if idx['sha256'] != a.expected_source_index_sha256: raise ValueError('Reviewed source index changed')
    d = read(a.source_index)
    if d['schema'] != 'Factory80RenderOnlyAuthorSourceIndexV1': raise ValueError('Wrong source schema')
    rows = []
    for name, original in d['runtime_files'].items():
        actual = ref(HERE/name)
        if (actual['bytes'], actual['sha256']) != (original['bytes'], original['sha256']):
            raise ValueError('Same-byte runtime alias changed')
        rows.append(actual)
    if ref(__file__) not in rows: raise ValueError('Caller is outside bound runtime')
    core = importlib.import_module('execution_core')
    if Path(inspect.getsourcefile(core)).absolute() != HERE/'execution_core.py': raise ValueError('Wrong actual core import')
    if a.building not in d['allowed_two_storey_buildings']: raise ValueError('Reviewed 29 N2 scope differs')
    inputs_ref = ref(a.source_inputs)
    if inputs_ref['sha256'] != a.expected_source_inputs_sha256: raise ValueError('Explicit ROOT actual input vector changed')
    inputs = read(a.source_inputs)
    if inputs['schema'] != 'Factory80RootActualN2RenderOnlyInputBindingsV1': raise ValueError('Wrong ROOT input schema')
    if inputs['source_unchanged'] is not True or not inputs['source_refs_before'] or inputs['source_refs_before'] != inputs['source_refs_after']:
        raise ValueError('Actual ROOT STD input source vector incomplete/changed')
    if type(inputs['source_count']) is not int or inputs['source_count'] != len(inputs['source_refs_before']):
        raise ValueError('ROOT actual input source count differs')
    for r in inputs['source_refs_before']: check(r)
    matches = [x for x in inputs['rows'] if x['building'] == a.building]
    if len(matches) != 1: raise ValueError('Unique ROOT actual root/AUTHORING binding required')
    row = matches[0]
    check(row['root_ref']); check(row['authoring_ref'])
    handoff_ref = ref(a.asset_handoff)
    expected = d['immutable_owned_asset_handoff_ref']
    if (handoff_ref['bytes'], handoff_ref['sha256']) != (expected['bytes'], expected['sha256']):
        raise ValueError('Owned asset handoff same-byte alias differs')
    handoff = read(a.asset_handoff)
    if handoff['schema'] != 'Factory80ActualOwnedPBRHDRIAssetHandoffV1' or handoff['status'] != 'ACTUAL_LOCAL_GENERATION_AND_INDEPENDENT_DECODE_COMPLETE_NOT_BOUND':
        raise ValueError('Wrong actual owned asset handoff')
    if type(handoff['actual_decoder_signed_exit']) is not int or handoff['actual_decoder_signed_exit'] != 0:
        raise ValueError('Actual independent asset decode is not signed0')
    assets = {}; aliases = []
    for x in handoff['assets']:
        key = (x['asset'], x['role'])
        if key in assets or x['license'] != OWNER: raise ValueError('Duplicate/unowned texture role')
        original = x['file_ref']; basename = original['path'].replace('\\', '/').rsplit('/', 1)[-1]
        actual = ref(a.asset_root/basename)
        if (actual['bytes'], actual['sha256']) != (original['bytes'], original['sha256']):
            raise ValueError('Actual copied texture/HDRI differs')
        wanted = 'sRGB' if x['role'] == 'albedo' else ('linear_relative_radiance' if x['role'] == 'environment_radiance' else 'raw_linear_data')
        if x['color_space'] != wanted: raise ValueError('Owned color-space role differs')
        assets[key] = actual; aliases.append({'immutable_source_ref': original, 'runtime_ref': actual})
    wanted = {(n, r) for n in ASSET_NAMES for r in ROLES} | {('analytic_daylight_sky', 'environment_radiance')}
    if set(assets) != wanted: raise ValueError('Exactly 13 owned asset roles required')
    protected = unique([idx, inputs_ref, *rows, handoff_ref, *assets.values(), row['root_ref'], row['authoring_ref'], ref(MAIN)])
    return core, d, row, assets, aliases, protected


def live_lease(a):
    r = ref(a.lease_receipt)
    if r['sha256'] != a.lease_receipt_sha256: raise ValueError('CPU lease receipt changed')
    d = read(a.lease_receipt); pid, fd = d['actual_parent_pid'], d['actual_parent_fd']
    if type(pid) is not int or type(fd) is not int or pid != os.getppid(): raise ValueError('Wrong direct lease owner')
    s = os.stat(f'/proc/{pid}/fd/{fd}')
    if (s.st_dev, s.st_ino) != CPU_ID: raise ValueError('Live CPU inode differs')
    text = Path(f'/proc/{pid}/fdinfo/{fd}').read_text()
    if not any('FLOCK' in x and 'WRITE' in x and re.search(r'\b'+str(pid)+r'\b', x) for x in text.splitlines()):
        raise ValueError('No actual parent WRITE FLOCK')


def mesh_triangles(stage, prim, UsdGeom, Usd, Gf):
    mesh = UsdGeom.Mesh(prim); pts = mesh.GetPointsAttr().Get()
    counts = mesh.GetFaceVertexCountsAttr().Get(); indices = mesh.GetFaceVertexIndicesAttr().Get()
    if pts is None or counts is None or indices is None: raise ValueError('Missing mesh geometry')
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    world = [matrix.Transform(Gf.Vec3d(p)) for p in pts]; faces = []; at = 0
    for count in counts:
        face = [world[indices[at+j]] for j in range(count)]; at += count
        if count < 3: raise ValueError('Nonpolygon target face')
        faces.append(face)
    if at != len(indices): raise ValueError('Mesh index/count inconsistency')
    return faces


def inside_xy(x, y, triangles):
    for a, b, c in triangles:
        d = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if d == 0: continue
        u = ((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/d
        v = ((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/d
        if u >= 0 and v >= 0 and u+v <= 1: return True
    return False


def surface_targets(stage, author, UsdGeom):
    rows = []; slabs = []
    for key in ['Floor1Slab', 'Floor2Slab']:
        info = author['cut_meshes'][key]; path = info.get('visual_path', info['path'])
        slabs.append(path); rows.append((path, 'concrete'))
    for root, material in [('/World/FactoryStructure/Columns', 'painted_steel'),
            ('/World/FactoryStructure/Trusses', 'painted_steel'),
            ('/World/FactoryStructure/Roof', 'painted_steel'),
            ('/World/MezzanineRail', 'safety_yellow')]:
        p = stage.GetPrimAtPath(root)
        if not p: raise ValueError('Required actual synthetic structure absent: '+root)
        from pxr import Usd
        rows.extend((str(q.GetPath()), material) for q in Usd.PrimRange(p) if q.IsA(UsdGeom.Mesh))
    if len({p for p, _ in rows}) != len(rows): raise ValueError('Repeated render binding target')
    for path, _ in rows:
        p = stage.GetPrimAtPath(path)
        if not p or not p.IsA(UsdGeom.Mesh) or not p.IsActive() or p.IsInstanceProxy(): raise ValueError('Invalid visual binding target '+path)
        image = UsdGeom.Imageable(p)
        if image.ComputePurpose() == 'guide' or image.ComputeVisibility() == 'invisible': raise ValueError('Do not bind collision-only/invisible mesh '+path)
        if not (path in slabs or path.startswith('/World/FactoryStructure/') or path.startswith('/World/MezzanineRail/')):
            raise ValueError('Native/equipment target forbidden')
    return rows, slabs


def make_material(stage, name, assets, UsdShade, Sdf, Gf):
    mat = UsdShade.Material.Define(stage, OWN+'/Looks/'+name)
    surface = UsdShade.Shader.Define(stage, str(mat.GetPath())+'/Surface'); surface.CreateIdAttr('UsdPreviewSurface')
    surface.CreateInput('useSpecularWorkflow', Sdf.ValueTypeNames.Int).Set(0)
    surface.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(1.0)
    reader = UsdShade.Shader.Define(stage, str(mat.GetPath())+'/ReadST'); reader.CreateIdAttr('UsdPrimvarReader_float2')
    reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
    reader_out = reader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
    for role in ROLES:
        tex = UsdShade.Shader.Define(stage, str(mat.GetPath())+'/'+role); tex.CreateIdAttr('UsdUVTexture')
        tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(assets[(name, role)]['path']))
        tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB' if role == 'albedo' else 'raw')
        tex.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set('repeat'); tex.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set('repeat')
        tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader_out)
        if role == 'normal':
            tex.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, 2, 2, 1))
            tex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 0))
        channel = 'rgb' if role in ['albedo', 'normal'] else 'r'
        output = tex.CreateOutput(channel, Sdf.ValueTypeNames.Float3 if channel == 'rgb' else Sdf.ValueTypeNames.Float)
        destination = {'albedo': ('diffuseColor', Sdf.ValueTypeNames.Color3f), 'normal': ('normal', Sdf.ValueTypeNames.Normal3f),
            'roughness': ('roughness', Sdf.ValueTypeNames.Float), 'metallic': ('metallic', Sdf.ValueTypeNames.Float)}[role]
        surface.CreateInput(*destination).ConnectToSource(output)
    mat.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput('surface', Sdf.ValueTypeNames.Token))
    return mat


def uv_values(faces, Gf):
    values = []
    for face in faces:
        n = Gf.Vec3d(0)
        for i in range(1, len(face)-1):
            n = Gf.Cross(face[i]-face[0], face[i+1]-face[0])
            if n.GetLength() > 0: break
        if n.GetLength() == 0: raise ValueError('Degenerate authored target face; no fake UV success')
        axis = max(range(3), key=lambda i: abs(n[i])); sign = 1 if n[axis] >= 0 else -1
        uv_axes = {0: (1, 2), 1: (2, 0), 2: (0, 1)}[axis]
        values.extend(Gf.Vec2f(p[uv_axes[0]]/2., sign*p[uv_axes[1]]/2.) for p in face)
    return values


def signature(stage, allowed, UsdGeom, Usd):
    h = hashlib.sha256(); rows = 0
    for p in stage.TraverseAll():
        path = str(p.GetPath())
        if path == OWN or path.startswith(OWN+'/'): continue
        apis = [x for x in p.GetAppliedSchemas() if not (path in allowed and x == 'MaterialBindingAPI')]
        header = (path, p.GetTypeName(), p.IsActive(), p.IsInstanceable(), apis)
        h.update(repr(header).encode()); rows += 1
        for prop in sorted(p.GetProperties(), key=lambda q: q.GetName()):
            if prop.GetName() in allowed.get(path, set()): continue
            if isinstance(prop, Usd.Attribute):
                data = (prop.GetName(), str(prop.GetTypeName()), repr(prop.GetAllMetadata()), repr(prop.Get()),
                    [(t, repr(prop.Get(t))) for t in prop.GetTimeSamples()])
            else: data = (prop.GetName(), repr(prop.GetAllMetadata()), [str(x) for x in prop.GetTargets()])
            h.update(repr(data).encode())
        if UsdGeom.Xformable(p):
            h.update(repr(UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default())).encode())
    return {'sha256': h.hexdigest(), 'prim_count': rows,
        'scope': 'Original prim/type/active/APIs (only applied MaterialBindingAPI addition excluded), all non-whitelisted properties/time samples and world transforms; no own added render subtree.'}


def normalized_flatten_exact(base_flat, derived_flat, allowed, api_paths, Usd, Sdf, inactive_light_paths):
    original = base_flat.ExportToString(); norm = Sdf.Layer.CreateAnonymous('render_normalization.usda')
    norm.ImportFromString(derived_flat.ExportToString()); temp = Usd.Stage.Open(norm)
    if not temp.RemovePrim(OWN): raise ValueError('Unable to remove own added render subtree in normalization copy')
    omitted_visibility = []
    for path, props in allowed.items():
        source_spec, derived_spec = base_flat.GetPrimAtPath(path), norm.GetPrimAtPath(path)
        if not source_spec and not derived_spec and path in inactive_light_paths and props == {'visibility'}:
            omitted_visibility.append(path)
            continue
        if not source_spec or not derived_spec:
            raise ValueError('Original/derived normalization prim spec missing or asymmetric: '+path)
        p = temp.GetPrimAtPath(path)
        if not p:
            raise ValueError('Normalization stage prim is invalid despite existing specs: '+path)
        for name in props:
            prop_path = Sdf.Path(path).AppendProperty(name); p.RemoveProperty(name)
            if base_flat.GetPropertyAtPath(prop_path):
                if not Sdf.CopySpec(base_flat, prop_path, norm, prop_path): raise ValueError('Unable to restore exact original visual property')
    for path in api_paths:
        original_spec, restored = base_flat.GetPrimAtPath(path), norm.GetPrimAtPath(path)
        if not original_spec or not restored:
            raise ValueError('Active material/UV normalization prim spec missing: '+path)
        if original_spec.HasInfo('apiSchemas'): restored.SetInfo('apiSchemas', original_spec.GetInfo('apiSchemas'))
        else: restored.ClearInfo('apiSchemas')
    actual = norm.ExportToString()
    if actual != original: raise ValueError('Whole normalized Flatten differs; no tolerance/ignored geometry edits allowed')
    return {'original_flatten_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'normalized_derived_flatten_sha256': hashlib.sha256(actual.encode()).hexdigest(), 'whole_string_exact': True,
        'original_inactive_light_visibility_omitted_in_both_Flatten': omitted_visibility,
        'normalization': 'Only delete own render subtree, restore each explicit material/UV/light-visibility property and validated MaterialBindingAPI metadata from original Flatten.'}


def worker(a):
    core, index, bound, assets, aliases, initial = binding(a); live_lease(a)
    if sys.platform != 'linux' or os.environ.get('CUDA_VISIBLE_DEVICES') != '': raise ValueError('Standalone MAIN CPU only')
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt
    result = {'schema': 'Factory80ActualRenderOnlyCPUAuthorResultV1', 'building': a.building,
        'actual_reader_pid': os.getpid(), 'status': 'NOT_EXECUTED', 'renderer_V1_V5_device': 'NOT_EXECUTED', 'entire_task_stop': False}
    protected = list(initial)
    try:
        author = read(bound['authoring_ref']['path'])
        if author['stage'] != bound['root_ref'] or author['building'] != a.building or author['storeys'] != 2:
            raise ValueError('Actual canonical author binding differs')
        base = Usd.Stage.Open(bound['root_ref']['path'], Usd.Stage.LoadAll)
        if not base or base.GetPrimAtPath(OWN): raise ValueError('Invalid original stage or own namespace collision')
        layers = []
        for layer in base.GetUsedLayers():
            if layer.anonymous: continue
            name = layer.realPath or layer.identifier
            # Protect the real package bytes, not a nonexistent USDZ member file.
            disk = name.split('[', 1)[0]
            if not Path(disk).is_absolute(): raise ValueError('Unresolved/nonabsolute used layer '+name)
            layers.append({'identifier': layer.identifier, 'file_ref': ref(disk)})
        protected = unique([*initial, *[x['file_ref'] for x in layers]])
        result['source_refs_before'] = protected; result['actual_used_layer_bindings'] = layers
        core.save(a.output/'ACTUAL_SOURCE_BEFORE.json', {'source_refs': protected, 'used_layer_bindings': layers})
        layer = Sdf.Layer.CreateAnonymous('owned_render_input.usda')
        layer.subLayerPaths = [bound['root_ref']['path']]
        layer.defaultPrim = base.GetRootLayer().defaultPrim
        for key in base.GetRootLayer().pseudoRoot.ListInfoKeys():
            if key not in ('subLayers', 'subLayerOffsets'):
                layer.pseudoRoot.SetInfo(key, base.GetRootLayer().pseudoRoot.GetInfo(key))
        stage = Usd.Stage.Open(layer, Usd.Stage.LoadAll)
        if (UsdGeom.GetStageMetersPerUnit(stage), UsdGeom.GetStageUpAxis(stage)) != (UsdGeom.GetStageMetersPerUnit(base), UsdGeom.GetStageUpAxis(base)):
            raise ValueError('Composed units/up-axis changed')
        stage.SetEditTarget(layer); UsdGeom.Scope.Define(stage, OWN)
        targets, slabs = surface_targets(base, author, UsdGeom)
        allowed = {p: {'material:binding', 'primvars:st', 'primvars:st:indices'} for p, _ in targets}
        originals = {p: list(base.GetPrimAtPath(p).GetAppliedSchemas()) for p, _ in targets}
        old_lights = [p for p in base.TraverseAll() if p.HasAPI(UsdLux.LightAPI)]
        for p in old_lights: allowed[str(p.GetPath())] = {'visibility'}
        before_signature = signature(base, allowed, UsdGeom, Usd)
        materials = {n: make_material(stage, n, assets, UsdShade, Sdf, Gf) for n in ASSET_NAMES}
        bindings = []
        for path, name in targets:
            prim = stage.GetPrimAtPath(path); faces = mesh_triangles(base, base.GetPrimAtPath(path), UsdGeom, Usd, Gf)
            uv = uv_values(faces, Gf); pv = UsdGeom.PrimvarsAPI(prim).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
            pv.Set(Vt.Vec2fArray(uv)); pv.SetIndices(Vt.IntArray())
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(materials[name])
            derived_apis = list(prim.GetAppliedSchemas())
            if derived_apis.count('MaterialBindingAPI') != 1 or [x for x in derived_apis if x != 'MaterialBindingAPI'] != [x for x in originals[path] if x != 'MaterialBindingAPI']:
                raise ValueError('API delta exceeds only MaterialBindingAPI')
            bindings.append({'mesh_path': path, 'material_path': str(materials[name].GetPath()), 'UV_interpolation': 'faceVarying',
                'UV_value_count': len(uv), 'UV_physical_tile_m': 2., 'UV_method': 'dominant face-normal signed world-axis projection; no vertex/face/normal edits',
            'original_applied_APIs': originals[path], 'derived_applied_APIs': derived_apis})
        for p in old_lights: UsdGeom.Imageable(stage.GetPrimAtPath(p.GetPath())).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render'], useExtentsHint=False)
        columns = []
        for row in author['structure']:
            if row['type'] != 'column': continue
            p = base.GetPrimAtPath(row['path'])
            if not p or not p.IsA(UsdGeom.Mesh): raise ValueError('Actual authored column absent')
            r = bbox.ComputeWorldBound(p).ComputeAlignedRange()
            if r.IsEmpty(): raise ValueError('Actual column bbox empty')
            lo, hi = r.GetMin(), r.GetMax()
            columns.append({'path': row['path'], 'min': list(lo), 'max': list(hi), 'xy': [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2]})
        xs = sorted({q['xy'][0] for q in columns}); ys = sorted({q['xy'][1] for q in columns})
        if len(xs) < 2 or len(ys) < 2: raise ValueError('Measured column bay grid absent')
        roof = bbox.ComputeWorldBound(base.GetPrimAtPath('/World/FactoryStructure/Roof')).ComputeAlignedRange()
        if roof.IsEmpty(): raise ValueError('Actual roof bbox absent')
        height = float(author['floor_to_floor_m']); faces = mesh_triangles(base, base.GetPrimAtPath(slabs[1]), UsdGeom, Usd, Gf)
        tops = [(f[0], f[i], f[i+1]) for f in faces for i in range(1, len(f)-1) if all(float(p[2]) == height for p in f)]
        if not tops: raise ValueError('No exact actual Floor2 top triangles for light placement')
        lights = []
        dome = UsdLux.DomeLight.Define(stage, OWN+'/Lights/HDRI')
        dome.CreateTextureFileAttr(Sdf.AssetPath(assets[('analytic_daylight_sky', 'environment_radiance')]['path']))
        dome.CreateTextureFormatAttr('latlong'); dome.CreateIntensityAttr(1.0); dome.CreateExposureAttr(0.)
        lights.append({'path': str(dome.GetPath()), 'type': 'DomeLight', 'intensity': 1., 'exposure': 0., 'relative_HDRI_uncalibrated': True})
        sun = UsdLux.DistantLight.Define(stage, OWN+'/Lights/Solar'); sun.CreateIntensityAttr(1800.); sun.CreateExposureAttr(0.); sun.CreateAngleAttr(.532)
        az, el = math.radians(35), math.radians(35); direction = Gf.Vec3d(-math.cos(el)*math.cos(az), -math.cos(el)*math.sin(az), -math.sin(el))
        UsdGeom.Xformable(sun.GetPrim()).AddOrientOp().Set(Gf.Quatf(Gf.Rotation(Gf.Vec3d(0, 0, -1), direction).GetQuat()))
        lights.append({'path': str(sun.GetPath()), 'type': 'DistantLight', 'intensity': 1800., 'exposure': 0., 'emission_world_direction': list(direction), 'source_relative_HDRI_solar_not_photometric_calibration': True})
        for ix, (x0, x1) in enumerate(zip(xs, xs[1:])):
            for iy, (y0, y1) in enumerate(zip(ys, ys[1:])):
                x, y = (x0+x1)/2, (y0+y1)/2
                specs = [('roof', float(roof.GetMin()[2])-.8, 2.4, 1.2, 1800.)]
                if inside_xy(x, y, tops): specs.append(('under_mezzanine', height-.6, 2., 1., 1200.))
                for kind, z, width, length, intensity in specs:
                    p = UsdLux.RectLight.Define(stage, OWN+f'/Lights/{kind}_{ix}_{iy}')
                    p.CreateWidthAttr(width); p.CreateHeightAttr(length); p.CreateIntensityAttr(intensity); p.CreateExposureAttr(0.)
                    amber=(kind=='under_mezzanine' and (ix+2*iy)%4==0)
                    p.CreateColorAttr(Gf.Vec3f(1.0,.53,.16) if amber else Gf.Vec3f(.78,.86,1.0))
                    UsdGeom.Xformable(p.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(x, y, z))
                    actual_xyz = list(UsdGeom.Xformable(p.GetPrim()).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation())
                    if actual_xyz != [x, y, z]: raise ValueError('Actual added light world placement differs')
                    lights.append({'path': str(p.GetPath()), 'type': 'RectLight', 'kind': kind, 'actual_xyz_m': actual_xyz,
                        'width_m': p.GetWidthAttr().Get(), 'height_m': p.GetHeightAttr().Get(),
                        'intensity': p.GetIntensityAttr().Get(), 'exposure': p.GetExposureAttr().Get(), 'actual_color':list(p.GetColorAttr().Get()),'amber_accent':amber, 'emission_world_direction': [0, 0, -1]})
        cameras = []
        for s in author['stairs']:
            p = base.GetPrimAtPath(s['root']); r = bbox.ComputeWorldBound(p).ComputeAlignedRange()
            if r.IsEmpty(): raise ValueError('Actual stair render bbox absent')
            lo, hi = r.GetMin(), r.GetMax(); center = [(lo[j]+hi[j])/2 for j in range(3)]
            radius = max(hi[j]-lo[j] for j in range(3))/2.; distance = max(radius*3.4, 4.)
            for angle in [35, 125, 215, 305]:
                t = math.radians(angle); eye = [center[0]+distance*math.cos(t), center[1]+distance*math.sin(t), center[2]+radius*1.3]
                hit = [c['path'] for c in columns if segment_box(eye, center, c['min'], c['max'])]
                cameras.append({'stair_root': s['root'], 'eye_xyz_m': eye, 'target_xyz_m': center,
                    'column_center_ray_intersections': hit, 'proposal_geometric_clear': not hit,
                    'required_actual_subject_image_fraction': [.30, .60], 'actual_subject_fraction': None,
                    'image_AOV_other_occlusion_and_V5': 'NOT_EXECUTED'})
        after_signature = signature(stage, allowed, UsdGeom, Usd)
        if before_signature != after_signature: raise ValueError('Original non-render/geometry/collision/pose signature differs')
        # False omits only USD's generated root-filename documentation comment.
        # No source-authored metadata/property or geometry is omitted.
        proof = normalized_flatten_exact(base.Flatten(False), stage.Flatten(False), allowed, [p for p, _ in targets], Usd, Sdf, {str(p.GetPath()) for p in old_lights if not p.IsActive()})
        shader_readbacks = []
        for material in materials.values():
            for p in Usd.PrimRange(material.GetPrim()):
                if not p.IsA(UsdShade.Shader): continue
                shader_readbacks.append({'path': str(p.GetPath()), 'id': UsdShade.Shader(p).GetIdAttr().Get(),
                    'inputs': [{'name': q.GetBaseName(), 'value': repr(q.Get()),
                        'actual_connections': [str(x) for x in q.GetAttr().GetConnections()]} for q in UsdShade.Shader(p).GetInputs()]})
        output_layer = a.output/'render_only.usda'
        if output_layer.exists(): raise ValueError('No render-layer overwrite')
        if not layer.Export(str(output_layer)): raise ValueError('OWN fresh Sdf.Export failed')
        reopened = Usd.Stage.Open(str(output_layer), Usd.Stage.LoadAll)
        if not reopened or reopened.GetRootLayer().subLayerPaths != [bound['root_ref']['path']]:
            raise ValueError('Exported absolute canonical sublayer binding differs')
        if signature(reopened, allowed, UsdGeom, Usd) != before_signature:
            raise ValueError('Actual exported layer composition source signature differs')
        export_proof = normalized_flatten_exact(base.Flatten(False), reopened.Flatten(False), allowed, [p for p, _ in targets], Usd, Sdf, {str(p.GetPath()) for p in old_lights if not p.IsActive()})
        result.update({'status': 'ACTUAL_RENDER_ONLY_CPU_LAYER_AUTHORED_NOT_RENDERED', 'render_layer_ref': ref(output_layer),
            'canonical_absolute_sublayer': bound['root_ref'], 'source_geometry_collision_transform_signature_before': before_signature,
            'source_geometry_collision_transform_signature_after': after_signature, 'whole_flatten_normalization': proof,
            'actual_export_reopen_whole_flatten_normalization': export_proof,
            'allowed_existing_property_opinions': {k: sorted(v) for k, v in allowed.items()},
            'material_bindings': bindings, 'actual_shader_input_readbacks': shader_readbacks,
            'light_rows': lights, 'actual_light_count': len(lights), 'actual_column_boxes': columns,
            'original_lights_render_visibility_only_disabled': [str(p.GetPath()) for p in old_lights],
            'camera_candidates': cameras, 'asset_samebyte_aliases': aliases,
            'equipment_Native_material_pose_geometry_source_preserved': True,
            'render_settings_registry_enum_exposure_calibration': 'PENDING_ACTUAL_SDK_REGISTRY_BINDING; no renderer settings applied'})
    except Exception as exc:
        result.update({'status': 'ORDINARY_RENDER_INPUT_PREPARATION_FAILURE', 'exception': {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}})
    finally:
        try:
            after = [ref(x['path']) for x in protected]; result['source_refs_after'] = after
            result['source_unchanged'] = protected == after
            if not result['source_unchanged']: result['status'] = 'ORDINARY_SOURCE_CHANGED_FAILURE'
        except Exception as exc:
            result.update({'status': 'ORDINARY_AFTER_SOURCE_RECHECK_FAILURE', 'after_source_exception': repr(exc)})
        result['source_count'] = len(protected); result['utc'] = core.stamp()
        core.save(a.output/'ACTUAL_RENDER_ONLY_CPU_AUTHOR_RESULT.json', result)
    return 0 if result['status'] == 'ACTUAL_RENDER_ONLY_CPU_LAYER_AUTHORED_NOT_RENDERED' else 1


def segment_box(eye, target, lower, upper):
    a, b = 0., 1.
    for i in range(3):
        d = target[i]-eye[i]
        if d == 0:
            if not lower[i] <= eye[i] <= upper[i]: return False
            continue
        u, v = (lower[i]-eye[i])/d, (upper[i]-eye[i])/d
        if u > v: u, v = v, u
        a, b = max(a, u), min(b, v)
        if a > b: return False
    return True


def parent(a):
    core, index, bound, assets, aliases, before = binding(a)
    if sys.platform != 'linux' or str(a.main_python.absolute()) != MAIN: raise ValueError('Use lexical Linux MAIN entry')
    a.output = a.output.absolute(); a.output.mkdir(parents=True, exist_ok=False)
    import fcntl
    fd = None; acquired = False; result = {'schema': 'Factory80ActualRenderOnlyCPUParentCompletionV1', 'status': 'NOT_EXECUTED',
        'building': a.building, 'entire_task_stop': False, 'SDK_GPU_render_V1_V5': 'NOT_EXECUTED', 'source_refs_before': before}
    try:
        fd = os.open(CPU_LOCK, os.O_RDWR | os.O_NOFOLLOW); st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != CPU_ID: raise ValueError('Existing CPU lock identity differs')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); acquired = True
        lease = a.output/'ACTUAL_CPU_PARENT_LEASE.json'
        core.save(lease, {'actual_parent_pid': os.getpid(), 'actual_parent_fd': fd, 'device': st.st_dev, 'inode': st.st_ino,
            'actual_fdinfo': Path(f'/proc/{os.getpid()}/fdinfo/{fd}').read_text(), 'utc': core.stamp()})
        argv = [str(a.main_python.absolute()), str(Path(__file__).absolute()), '--internal-child', '--source-index', str(a.source_index.absolute()),
            '--expected-source-index-sha256', a.expected_source_index_sha256, '--building', a.building,
            '--source-inputs', str(a.source_inputs.absolute()), '--expected-source-inputs-sha256', a.expected_source_inputs_sha256,
            '--asset-handoff', str(a.asset_handoff.absolute()), '--asset-root', str(a.asset_root.absolute()), '--output', str(a.output),
            '--lease-receipt', str(lease), '--lease-receipt-sha256', ref(lease)['sha256']]
        env = dict(os.environ); env['CUDA_VISIBLE_DEVICES'] = ''; env['PYTHONUNBUFFERED'] = '1'
        with (a.output/'CHILD.stdout').open('xb') as out, (a.output/'CHILD.stderr').open('xb') as err:
            code, raw = core.run_owned(argv, out, err, a.output/'ACTUAL_TRUE_CHILD_WAIT.json', env, str(HERE), wall_timeout_s=1800., termination_grace_s=60.)
        result['raw_wait_ref'] = ref(a.output/'ACTUAL_TRUE_CHILD_WAIT.json'); result['actual_signed_child_returncode'] = code
        result['stdout_ref'], result['stderr_ref'] = ref(a.output/'CHILD.stdout'), ref(a.output/'CHILD.stderr')
        after = [ref(x['path']) for x in before]; result['source_refs_after'] = after; result['source_unchanged'] = before == after
        if type(code) is not int or code != 0 or raw['actual_parent_wait_completed'] is not True or raw['own_parent_exception'] or raw['observed_owned_parent_interruptions'] or raw['actual_owned_child_actions']:
            raise ValueError('Actual render-input child did not complete clean signed wait0')
        doc = read(a.output/'ACTUAL_RENDER_ONLY_CPU_AUTHOR_RESULT.json')
        if doc['status'] != 'ACTUAL_RENDER_ONLY_CPU_LAYER_AUTHORED_NOT_RENDERED' or doc['source_unchanged'] is not True or doc['actual_reader_pid'] != raw['actual_child_pid']:
            raise ValueError('Actual input author completion ineligible')
        if not result['source_unchanged']: raise ValueError('Protected original/asset/source bytes changed')
        result.update({'status': 'ACTUAL_RENDER_ONLY_CPU_INPUT_PREPARATION_COMPLETED', 'actual_result_ref': ref(a.output/'ACTUAL_RENDER_ONLY_CPU_AUTHOR_RESULT.json')})
    except Exception as exc:
        result.update({'status': 'ORDINARY_RENDER_INPUT_PREPARATION_FAILURE', 'exception': {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}})
    finally:
        if fd is not None: os.close(fd)
        result['CPU_lease_acquired'] = acquired; result['CPU_descriptor_closed'] = fd is not None
        result['utc'] = core.stamp(); core.save(a.output/'ACTUAL_PARENT_COMPLETION.json', result)
    return 0 if result['status'] == 'ACTUAL_RENDER_ONLY_CPU_INPUT_PREPARATION_COMPLETED' else 1


def main():
    p = argparse.ArgumentParser(allow_abbrev=False); p.add_argument('--execute', action='store_true'); p.add_argument('--internal-child', action='store_true')
    p.add_argument('--source-index', type=Path); p.add_argument('--expected-source-index-sha256'); p.add_argument('--building')
    p.add_argument('--source-inputs', type=Path); p.add_argument('--expected-source-inputs-sha256')
    p.add_argument('--asset-handoff', type=Path); p.add_argument('--asset-root', type=Path); p.add_argument('--output', type=Path)
    p.add_argument('--main-python', type=Path, default=Path(MAIN)); p.add_argument('--lease-receipt', type=Path); p.add_argument('--lease-receipt-sha256')
    a = p.parse_args()
    if not a.execute and not a.internal_child:
        print(json.dumps({'status': 'SOURCE_ONLY_NOT_EXECUTED', 'PXR_SDK_render_science': False})); return 0
    if a.execute and a.internal_child: raise ValueError('Mutually exclusive parent/worker modes')
    if not all([a.source_index, a.expected_source_index_sha256, a.source_inputs, a.expected_source_inputs_sha256, a.building, a.asset_handoff, a.asset_root, a.output]):
        raise ValueError('Explicit reviewed source/canonical/assets/fresh output required')
    return worker(a) if a.internal_child else parent(a)


if __name__ == '__main__': raise SystemExit(main())
