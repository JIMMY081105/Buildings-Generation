"""New user metric: cohort A only; B is restoration/sealing, never mainM optimization."""
from collections import Counter
from pathlib import Path
from evidence_common import read_json
W=Path('/cephfs/shared/yixing/outputs/diversity54_20260908')
PRIMARY=('BrushedMetal','MetalBasic','Marble');SECONDARY=('Wood','WhitePlywood','BlackPlywood','BlondePlywood')
def populations(rows):
 r=read_json(W/'CAP8_AB_COHORT_REGISTRY_R1.json');groups=r['cohorts']
 assert len(rows)==54 and set(rows)==set(groups)
 a={b:s for b,s in rows.items() if groups[b]=='A'}
 verified={b:s for b,s in a.items() if r['baseline_proofs'][b]['actual_baseline_status']=='PASS'}
 b={bid:s for bid,s in rows.items() if groups[bid]=='B'}
 assert len(a)==40 and len(verified)==37 and len(b)==14
 return a,verified,b
def objective(rows):
 counts=Counter(rows.values());assert counts and all(len(s.split('|'))==5 for s in counts)
 return max(counts.values()),sum(s.split('|')[2] in SECONDARY for s in rows.values()),sorted(counts.values(),reverse=True)+[0]*(len(rows)-len(counts))
def select(before,after,bid,style):
 a0,v0,b0=populations(before);a1,v1,b1=populations(after)
 group='A' if bid in a0 else 'B';assert style in ('MATCH','ACCEPTABLE','CLASH')
 selected=objective(a1)<=objective(a0) if group=='A' else after[bid].split('|')[2] in PRIMARY and style in ('MATCH','ACCEPTABLE')
 if group=='B':assert a0==a1
 return selected,dict(schema='OwnedABMaterialSelectionV1',cohort=group,selected=selected,rule='A主指标先降M，同M少用第二梯队；B只修搭配并清CLASH，不参与主M优化。',baseline_nine_pass_source_count=37,baseline_a40_g25_exceptions_explicit=True,before=dict(a40_m=objective(a0)[0],verified_a37_m=objective(v0)[0],all54_m=objective(before)[0],a_secondary_material_buildings=objective(a0)[1]),after=dict(a40_m=objective(a1)[0],verified_a37_m=objective(v1)[0],all54_m=objective(after)[0],a_secondary_material_buildings=objective(a1)[1]),capacity_checks_replaced=False,geometry_gate_predicates_replaced=False,formal_acceptance_added=False,cannot_support='Selection ordering does not turn three actualG25FAIL records intoPASS, establish training suitability or formal acceptance.')
