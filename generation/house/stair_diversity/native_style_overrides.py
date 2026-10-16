"""Sanctioned style adapter: official Infinigen factory attributes only."""
from pathlib import Path
import numpy as np
from infinigen.assets.composition import material_assignments
from evidence_common import ref, require_phase


def surface_registry():
    result={}
    for pool in ('step','rail','tread','side','handrail','post','glasses'):
        for cls, weight in getattr(material_assignments,pool):
            name=getattr(cls,'__name__',None)
            if name:
                if name in result and result[name] is not cls:
                    raise RuntimeError(f'Ambiguous native material factory: {name}')
                result[name]=cls
    return result


def add_arguments(parser):
    parser.add_argument('--portfolio-plan',type=Path,required=True)
    parser.add_argument('--scene-capacity',type=Path,required=True)
    parser.add_argument('--building',required=True)
    parser.add_argument('--support-type',choices=('solid','side','hole','double-rail','single-rail'))
    parser.add_argument('--handrail-type',choices=('vertical-post','glass','horizontal-post'))
    for role in ('step','tread','handrail'):
        parser.add_argument(f'--{role}-material',choices=sorted(surface_registry()))
    parser.add_argument('--has-rail',choices=('true','false'))
    parser.add_argument('--has-sides',choices=('true','false'))
    parser.add_argument('--mirror',choices=('true','false'))


def public_value(value):
    if value is None or isinstance(value,(str,int,float,bool)):
        return value
    if isinstance(value,np.generic):
        return value.item()
    if isinstance(value,np.ndarray):
        return value.tolist()
    if isinstance(value,(list,tuple)):
        return [public_value(v) for v in value]
    if isinstance(value,dict):
        return {str(k):public_value(v) for k,v in value.items()}
    attributes={key:public_value(v) for key,v in vars(value).items() if not key.startswith('_')} if hasattr(value,'__dict__') else {}
    return {'class':type(value).__module__+'.'+type(value).__name__,'attributes':attributes}


def effective_attributes(factory):
    result={key:public_value(value) for key,value in sorted(vars(factory).items())}
    result['constants_public_scalars']={key:public_value(getattr(factory.constants,key)) for key in dir(factory.constants)
                                       if not key.startswith('_') and isinstance(getattr(factory.constants,key),(str,int,float,bool,np.generic))}
    return result


def apply_overrides(factory,args):
    plan=require_phase(args.portfolio_plan,'Residential54DiversityPortfolioPlanV1')
    capacity=require_phase(args.scene_capacity,'Residential54SceneCapacityV1')
    row=next(r for r in plan['rows'] if r['building']==args.building)
    cap=next(r for r in capacity['rows'] if r['building']==args.building)
    if row['floor_height_m']!=args.height or cap['floor_height_m']!=args.height:
        raise RuntimeError('Generation height differs from measured building height')
    before=effective_attributes(factory)
    if args.support_type:
        factory.support_type=args.support_type
        factory.has_step=args.support_type in ('solid','hole')
        factory.has_sides=args.support_type in ('solid','hole','side')
        factory.has_rail=args.support_type in ('double-rail','single-rail')
    if args.handrail_type:
        factory.handrail_type=args.handrail_type
        factory.has_vertical_post=args.handrail_type=='vertical-post'
        factory.has_glasses=args.handrail_type=='glass'
        factory.has_bars=args.handrail_type=='horizontal-post'
    registry=surface_registry()
    for argument,attributes in (('step_material',('step_surface','side_surface','rail_surface')),
                                ('tread_material',('tread_surface',)),
                                ('handrail_material',('handrail_surface','post_surface'))):
        name=getattr(args,argument)
        if name:
            for attribute in attributes:
                setattr(factory,attribute,registry[name]())
    for argument,attribute in (('has_rail','has_rail'),('has_sides','has_sides'),('mirror','mirror')):
        if getattr(args,argument) is not None:
            setattr(factory,attribute,getattr(args,argument)=='true')
    factory.rot_z=0.0
    return {'portfolio_plan':ref(args.portfolio_plan),'scene_capacity':ref(args.scene_capacity),'building':args.building,
            'adapter':ref(__file__),'before':before,'effective':effective_attributes(factory),
            'requested_overrides':{key:getattr(args,key) for key in ('support_type','handrail_type','step_material','tread_material','handrail_material','has_rail','has_sides','mirror')},
            'variation_layer':'official factory attributes before create_asset, not post-hoc USD material edits',
            'geometry_scale_policy':'height recomputed by the unchanged native height adapter; object scale must remain identity'}
