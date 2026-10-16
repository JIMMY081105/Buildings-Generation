"""Translate unchanged native baked channels to the existing USD preview schema.

No palette, shader color, material choice, mesh, or collision changes are made.
Native bake calibration proves scalar PNGs are sRGB encoded and normals are raw.
The existing native emission bake writes the native Base Color graph directly;
the original shader graph is restored exactly after this one bake pass.
"""
from contextlib import contextmanager
from pathlib import Path
from pxr import Gf, Sdf, UsdShade
from infinigen.core.sim.exporters import usd_exporter
from infinigen.tools import export as native_export


def principled_nodes(tree, seen=None):
    if seen is None:
        seen = set()
    if tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            yield node
        elif node.type == 'GROUP' and node.node_tree:
            yield from principled_nodes(node.node_tree, seen)


@contextmanager
def preserve_base_color_during_native_diffuse_bake():
    original = native_export.bake_pass
    def bake_pass(obj, dest, img_size, bake_type, export_usd, export_name=None):
        if bake_type != 'DIFFUSE':
            return original(obj, dest, img_size, bake_type, export_usd, export_name)
        # COLOR of the native DIFFUSE pass includes the BSDF Fresnel weight.
        # Use the native baker's existing emission-pass mechanism to encode the
        # unmodified native Base Color graph itself, without lighting response.
        saved = []
        seen = set()
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            for node in principled_nodes(mat.node_tree, seen):
                tree = node.id_data
                outgoing = [(link.from_socket, link.to_socket) for link in node.outputs[0].links]
                if not outgoing:
                    continue
                emission = tree.nodes.new('ShaderNodeEmission')
                emission.inputs['Strength'].default_value = 1.0
                source = node.inputs['Base Color']
                if source.is_linked:
                    assert len(source.links) == 1
                    tree.links.new(source.links[0].from_socket, emission.inputs['Color'])
                else:
                    emission.inputs['Color'].default_value = source.default_value
                for link in list(node.outputs[0].links):
                    tree.links.remove(link)
                for from_socket, to_socket in outgoing:
                    tree.links.new(emission.outputs[0], to_socket)
                saved.append((tree, emission, outgoing))
        assert saved, 'No native Principled color graph to bake'
        special = native_export.SPECIAL_BAKE
        native_export.SPECIAL_BAKE = dict(special, DIFFUSE='Base Color')
        try:
            return original(obj, dest, img_size, bake_type, export_usd, export_name)
        finally:
            native_export.SPECIAL_BAKE = special
            for tree, emission, outgoing in saved:
                tree.nodes.remove(emission)
                for from_socket, to_socket in outgoing:
                    tree.links.new(from_socket, to_socket)
    native_export.bake_pass = bake_pass
    try:
        yield
    finally:
        native_export.bake_pass = original


class PreviewSchemaBuilder(usd_exporter.USDBuilder):
    def _bake_materials(self, asset, name, image_res):
        with preserve_base_color_during_native_diffuse_bake():
            return super()._bake_materials(asset, name, image_res)

    def _add_texture_map(self, texture_type, file, mtl_path, pbr_shader, st_reader):
        if not file.exists():
            return
        node_names = {'diffuse': 'diffuseTexture', 'normal': 'normalTexture', 'roughness': 'roughnessTexture',
                      'metallic': 'metallicTexture', 'transmission': 'transmissionTexture'}
        inputs = {'diffuse': ('diffuseColor', Sdf.ValueTypeNames.Color3f),
                  'normal': ('normal', Sdf.ValueTypeNames.Normal3f),
                  'roughness': ('roughness', Sdf.ValueTypeNames.Float),
                  'metallic': ('metallic', Sdf.ValueTypeNames.Float),
                  'transmission': ('opacity', Sdf.ValueTypeNames.Float)}
        node = UsdShade.Shader.Define(self.stage, str(mtl_path)+'/'+node_names[texture_type])
        node.CreateIdAttr('UsdUVTexture')
        parts = Path(file).parts
        relative = Path(*parts[parts.index('assets'):]).as_posix()
        node.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(relative)
        node.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(st_reader.ConnectableAPI(), 'result')
        for key in ('wrapS', 'wrapT'):
            node.CreateInput(key, Sdf.ValueTypeNames.Token).Set('repeat')
        node.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('raw' if texture_type == 'normal' else 'sRGB')
        if texture_type == 'normal':
            node.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, 2, 2, 1))
            node.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 0))
        if texture_type == 'transmission':
            node.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 1))
            node.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(1, 1, 1, 0))
        vector = texture_type in ('diffuse', 'normal')
        output = 'rgb' if vector else 'r'
        node.CreateOutput(output, Sdf.ValueTypeNames.Float3 if vector else Sdf.ValueTypeNames.Float)
        name, value_type = inputs[texture_type]
        pbr_shader.CreateInput(name, value_type).ConnectToSource(node.ConnectableAPI(), output)
        # The native block authors a string connected to a token-valued material
        # primvar name; use the specification's token type at both ends.
        st_reader.GetInput('varname').GetAttr().SetTypeName(Sdf.ValueTypeNames.Token)


@contextmanager
def preview_schema_export():
    original = usd_exporter.USDBuilder
    usd_exporter.USDBuilder = PreviewSchemaBuilder
    try:
        yield
    finally:
        usd_exporter.USDBuilder = original
