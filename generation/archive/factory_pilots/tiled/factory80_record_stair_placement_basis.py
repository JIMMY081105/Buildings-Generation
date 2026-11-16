"""Document pilot stair density transparently; never alter geometry or gates."""
import hashlib,json,math
from datetime import datetime,timezone
from pathlib import Path
W=Path('/cephfs/shared/yixing/outputs/factory80_20261011')
def ref(p):
    raw=p.read_bytes();return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def main():
    plan=json.loads((W/'TILING_PLAN.json').read_text());rows=[]
    for row in plan['rows'][:6]:
        n=row['grid_n'];pitch=row['tile_pitch_m'];area=n*n*pitch[0]*pitch[1]
        recommended=max(2,math.ceil(area/3600))
        assert row['storeys']==2 and row['planned_stair_count']==recommended
        authored=[]
        for a in range(1,4):
            root=W/'buildings'/row['building']/f'attempt_{a:02d}';path=root/'AUTHORING.json'
            if not path.is_file():continue
            actual=json.loads(path.read_text());source=ref(root/'root.usda')
            assert actual['stage']==source and actual['building']==row['building']
            stairs=actual['stairs'];count=actual['stair_count']
            assert count==recommended and len(stairs)==count and {s['index'] for s in stairs}==set(range(count))
            authored.append({'attempt':a,'authoring':ref(path),'source_stage':source,
                'actual_authoring_receipt_stair_count':count,
                'actual_authoring_receipt_positions':[{
                    'stair_id':f'S{s["index"]+1:02d}','root':s['root'],
                    'arrival_xyz_m':s['target_arrival_xyz_m'],'offset':s['stair_offset'],
                    'rotation_deg':s['stair_rotation_deg'],'placement_rule':s['placement_rule'],
                    'available_mezzanine_strip_depth_m':s['placement_available_mezzanine_strip_depth_m']
                } for s in stairs],
                'graph_distance_execution':'NOT MEASURED in this density readback; use source-bound DESCRIPTIVE_GRAPH_STAIR_DISTANCES.json',
                'cannot_support':'Authoring receipt readback is not an independent geometric or evacuation-distance measurement.'})
        rows.append({'building':row['building'],'planned_footprint_area_m2':area,
            'planned_stairs':row['planned_stair_count'],'quantitative_density_rule_stairs':recommended,
            'footprint_area_per_stair_m2':area/recommended,'actual_authored_attempts':authored,
            'cannot_support':'Footprint density does not prove usable area, maximum travel distance, unobstructed routes, or evacuation compliance.'})
    d={'schema':'Factory80PilotStairPlacementBasisV1','utc':datetime.now(timezone.utc).isoformat(),
       'status':'DESCRIPTIVE_BASIS_AND_ACTUAL_AUTHORING_READBACK_NO_ACCEPTANCE',
       'source_tiling_plan':ref(W/'TILING_PLAN.json'),'source_planning_script':ref(W/'scripts/plan_factory80_tiling.py'),
       'historical_original_count_rule':'grid_n <= 4: 2 stairs; otherwise: 3 stairs',
       'basis_recorded_after_initial_authoring':True,
       'provenance':'This quantitative explanation and external-reference review were documented after the original pilot plan and existing authoring. They are not claimed as preregistered decisions.',
       'pilot_quantitative_density_rule':{'minimum_stairs':2,'maximum_footprint_area_per_stair_m2':3600,
           'formula':'M = max(2, ceil(ground footprint area / 3600 m2))',
           'scope':'Only the first six two-storey pilots. Does not authorize building 7 or revise the deferred 74-building plan.',
           'basis':'Project density heuristic consistent with the actual 3780/6720/10500 m2 pilot footprints and original 2/2/3 counts. 3600 m2 is our heuristic, not a value prescribed by the cited standard.',
           'gate_added_or_threshold_changed':False},
       'external_references':[{'title':'OSHA 29 CFR 1910.36(b)(1)-(2)',
           'url':'https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.36',
           'actually_examined':'Official current page, paragraphs b(1) and b(2), read 2026-10-10 UTC',
           'paraphrase':'Workplaces generally need two separated exit routes; greater building size or an unsuitable arrangement can require more. The rule does not specify one stair per 3600 m2.',
           'application':'Supports separated redundancy as a design reference; industrial open stairs have not been demonstrated to be compliant exit routes.'}],
       'actual_placement_method':'Existing root authoring distributes native stairs across the facade at column-bay centres, fits actual shafts within the continuous mezzanine strip, retains a rear floor margin, and clears equipment only within actual approach/egress envelopes.',
       'distance_evidence_required':'Actual source-bound graph distance values, including unreachable nodes and connected components, remain required. Density and straight-line geometry never substitute for them.',
       'rows':rows,'formal_acceptance':'NOT EXECUTED',
       'cannot_support':'This supplementary description does not establish original preregistration, fire-code compliance, frozen gate PASS, physical locomotion, or completion of section 7.'}
    p=W/'evidence/STAIR_PLACEMENT_BASIS_FACTORY80.json'
    with p.open('x') as stream:json.dump(d,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':ref(p),'rows':len(rows),'basis_recorded_after_initial_authoring':True}))
if __name__=='__main__':main()
