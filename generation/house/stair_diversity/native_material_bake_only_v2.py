"""Call the unchanged native material-baking block without an asset export.

This intentionally never calls usd_exporter.export and never supplies a
visual_only flag. Collision remains in the referenced, fully exported primary
asset. The exact official shader construction block is executed unchanged.
"""
import ast
import copy
import inspect
from pathlib import Path
import numpy as np
from pxr import Sdf, UsdGeom
from infinigen.core.sim.exporters import usd_exporter
from evidence_common import ref, utc_now, write_new


def bake(blend_obj, blueprint, source_stage, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    builder = usd_exporter.USDBuilder(output_dir / 'assets')
    _, kinematic_root, metadata = usd_exporter.exputils.parse_sim_blueprint(blueprint)
    usd_exporter.SimBuilder.build(builder, blend_obj, metadata)
    root, _ = builder._construct_rigid_body_skeleton(kinematic_root)
    root = builder._wrap_in_body(root)
    builder._simplify_skeleton(root)
    assets = [asset for body in root for asset in body.assets]
    if len(assets) != 1:
        raise RuntimeError('Expected one original static native mesh for material reuse')
    vertices, faces, counts, labels, asset = builder._get_geometry_info(assets[0].attribs)
    if labels:
        raise RuntimeError('Material reuse expected the primary static, unlabelled geometry')
    stage = builder.stage
    world = UsdGeom.Xform.Define(stage, '/Asset/world').GetPrim()
    original_world = source_stage.GetPrimAtPath('/Asset/world')
    for prop in original_world.GetProperties():
        if prop.GetName() == 'xformOpOrder' or prop.GetName().startswith('xformOp:'):
            attr = original_world.GetAttribute(prop.GetName())
            world.CreateAttribute(attr.GetName(), attr.GetTypeName(), custom=False).Set(attr.Get())
    UsdGeom.Scope.Define(stage, '/Asset/world/visual')
    mesh = UsdGeom.Mesh.Define(stage, '/Asset/world/visual/geom_0')
    mesh.GetPointsAttr().Set(vertices)
    mesh.GetFaceVertexCountsAttr().Set(counts)
    mesh.GetFaceVertexIndicesAttr().Set(faces)
    mesh.GetSubdivisionSchemeAttr().Set('bilinear')
    exporter_file = Path(inspect.getfile(usd_exporter))
    text = exporter_file.read_text()
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'USDBuilder')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_add_mesh')
    start = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                 and n.value.func.attr == '_bake_materials')
    stop = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'colmeshes' for t in n.targets))
    body = copy.deepcopy(method.body[start:stop])
    if not body or any(isinstance(n, ast.Name) and n.id == 'visual_only' for b in body for n in ast.walk(b)):
        raise RuntimeError('Native material block extraction is invalid')
    program = compile(ast.Module(body=body, type_ignores=[]), str(exporter_file), 'exec')
    namespace = dict(vars(usd_exporter))
    namespace.update({'self': builder, 'asset': asset, 'unique_name': 'geom_0', 'image_res': 512,
                      'vismesh': mesh, 'mat_physics': usd_exporter.mtlphysics.get_material_properties(asset)})
    exec(program, namespace)
    usd = output_dir / 'staircase.usd'
    stage.GetRootLayer().Export(str(usd))
    metadata = output_dir / 'native_bake_metadata.json'
    write_new(metadata, {'schema': 'Residential54NativeMaterialOnlyExecutionV1', 'status': 'RECORDED',
              'generated_at_utc': utc_now(), 'official_exporter_source': ref(exporter_file), 'adapter': ref(__file__),
              'official_block_start_line': method.body[start].lineno, 'official_block_end_line': method.body[stop-1].end_lineno,
              'official_block_source': '\n'.join(text.splitlines()[method.body[start].lineno-1:method.body[stop-1].end_lineno]),
              'usd_exporter_export_called': False, 'visual_only_argument_supplied': False,
              'new_collision_generation': False, 'collision_source': 'Referenced primary native full export, verified separately',
              'image_resolution': 512})
    return usd, metadata
