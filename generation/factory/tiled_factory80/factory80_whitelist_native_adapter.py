"""Factory80 pre-generation native style adapter and actual Blender shader color trace."""
import json
import math
from pathlib import Path
from factory80_whitelist import ref, require
from native_style_overrides import surface_registry, effective_attributes

def add_arguments(parser):
    parser.add_argument('--portfolio-plan', type=Path, required=True)
    parser.add_argument('--scene-capacity', type=Path)
    parser.add_argument('--building', required=True)
    parser.add_argument('--support-type')
    parser.add_argument('--handrail-type')
    for role in ('step', 'tread', 'handrail'): parser.add_argument('--'+role+'-material')
    for flag in ('has-rail','has-sides','mirror'): parser.add_argument('--'+flag, choices=('true','false'))

def row_for(args):
    plan = json.loads(args.portfolio_plan.read_text())
    if plan['schema'] != 'Factory80SignaturePlanV1' or plan['status'] != 'PLANNED_PREFLIGHT_PASS':
        raise ValueError('Wrong or failed Factory80 pre-generation plan')
    row = next(row for row in plan['rows'] if row['building'] == args.building)
    require(row)
    if row['staircase_family'] != args.kind: raise ValueError('Generator family differs from preassignment')
    return row

def apply_overrides(factory, args):
    row = row_for(args)
    before = effective_attributes(factory)
    for field in ('support_type','handrail_type','step_material','tread_material','handrail_material'):
        requested = getattr(args, field)
        if requested is not None and requested != row[field]: raise ValueError('CLI differs from preassignment: '+field)
    factory.support_type = row['support_type']
    factory.has_step = row['support_type'] in ('solid','hole')
    factory.has_sides = row['support_type'] in ('solid','hole')
    factory.has_rail = row['support_type'] == 'double-rail'
    factory.handrail_type = row['handrail_type']
    factory.has_vertical_post = row['handrail_type'] == 'vertical-post'
    factory.has_glasses = False
    factory.has_bars = row['handrail_type'] == 'horizontal-post'
    registry = surface_registry()
    for role, attrs in (('step_material',('step_surface','side_surface','rail_surface')),('tread_material',('tread_surface',)),('handrail_material',('handrail_surface','post_surface'))):
        for attr in attrs: setattr(factory, attr, registry[row[role]]())
    # Preserve native geometry dimensions; orient only via explicit integration.
    if args.mirror is not None: factory.mirror = args.mirror == 'true'
    factory.rot_z = 0.0
    actual = dict(staircase_family=args.kind, support_type=factory.support_type, handrail_type=factory.handrail_type,
                  step_material=type(factory.step_surface).__name__, tread_material=type(factory.tread_surface).__name__, handrail_material=type(factory.handrail_surface).__name__)
    require(actual)
    return dict(portfolio_plan=ref(args.portfolio_plan), building=args.building, adapter=ref(__file__), before=before, effective=effective_attributes(factory), requested_overrides={key:row[key] for key in ('support_type','handrail_type','step_material','tread_material','handrail_material')}, actual_whitelist=actual, color_target_only=row['rgb_target_srgb'], geometry_scale_policy='Unchanged native height adaptation, identity scale; actual collision normalization/gates still required')

def linear(rgb):
    if len(rgb) != 3 or not all(isinstance(v,(float,int)) and math.isfinite(v) and 0 <= v <= 1 for v in rgb): raise ValueError('Invalid RGB')
    return [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]

def apply_native_rgb(obj, args):
    row = row_for(args)
    target = linear(row['rgb_target_srgb'])
    records = []
    for index, slot in enumerate(obj.material_slots):
        material = slot.material
        face_count = sum(poly.material_index == index for poly in obj.data.polygons)
        if not face_count: continue
        if material is None or not material.use_nodes: raise ValueError('Actual used native material has no shader nodes')
        nodes = []
        inventory = []
        def walk(tree, prefix, ancestors):
            if tree.as_pointer() in ancestors: raise ValueError('Cyclic actual material node group')
            ancestors = ancestors | {tree.as_pointer()}
            for node in tree.nodes:
                path = prefix+'/'+node.name
                inventory.append(dict(path=path, type=node.type))
                if node.type == 'BSDF_PRINCIPLED': nodes.append((tree,node,path))
                if node.type == 'GROUP' and node.node_tree is not None: walk(node.node_tree,path,ancestors)
        walk(material.node_tree,material.name,set())
        if not nodes: raise ValueError('Actual used native material has no Principled BSDF color input: '+json.dumps(inventory))
        node_records = []
        for tree,node,path in nodes:
            socket = node.inputs.get('Base Color')
            if socket is None: raise ValueError('Missing actual Base Color input')
            before = list(socket.default_value)
            removed = [{'from_node':link.from_node.name,'from_socket':link.from_socket.name} for link in socket.links]
            for link in list(socket.links): tree.links.remove(link)
            socket.default_value = [*target, 1.0]
            actual = list(socket.default_value)
            if socket.is_linked or any(abs(actual[i]-target[i]) > 1e-6 for i in range(3)): raise ValueError('Native RGB result differs from requested color')
            node_records.append(dict(node=node.name, actual_node_path=path, node_tree=tree.name, before_rgba_linear=before, removed_color_links=removed, actual_rgba_linear=actual, base_color_is_linked=socket.is_linked))
        records.append(dict(material=material.name, slot=index, actual_face_count=face_count, nodes=node_records, actual_node_inventory=inventory))
    if not records: raise ValueError('No used native materials were colored')
    return dict(schema='Factory80ActualNativeRGBV1', status='PASS', adapter=ref(__file__), building=args.building,
                rgb_target_srgb=row['rgb_target_srgb'], rgb_target_linear=target, actual_used_native_materials=records,
                phase='After native factory finalize/material role audit, before native official export and CPU texture bake',
                unsupported='This measures real native shader Base Color values. It does not establish exported texture appearance, actual RTX perceived color, physical gates or acceptance.')
