"""Owned ordering: reduce real A multiplicities before counting secondary materials."""
from collections import Counter
from cap8_ab_material_policy_r1_20261007 import populations, PRIMARY, SECONDARY

def objective(rows):
    counts = Counter(rows.values())
    assert counts and all(len(s.split('|')) == 5 for s in counts)
    vector = sorted(counts.values(), reverse=True) + [0] * (len(rows) - len(counts))
    secondary = sum(s.split('|')[2] in SECONDARY for s in rows.values())
    return max(counts.values()), vector, secondary

def select(before, after, bid, style):
    assert set(before) == set(after) and bid in before
    assert all(before[b] == after[b] for b in before if b != bid), 'One material selection may change only its exact building'
    a0, v0, b0 = populations(before)
    a1, v1, b1 = populations(after)
    group = 'A' if bid in a0 else 'B'
    assert style in ('MATCH', 'ACCEPTABLE', 'CLASH')
    if group == 'A':
        assert b0 == b1, 'B population is sealed and excluded from A optimization'
        selected = objective(a1) <= objective(a0)
    else:
        assert a0 == a1
        selected = after[bid].split('|')[2] in PRIMARY and style in ('MATCH', 'ACCEPTABLE')
    def metrics(a, verified, all_rows):
        obj = objective(a)
        return dict(a40_m=obj[0], verified_a37_m=objective(verified)[0], all54_m=objective(all_rows)[0],
                    a_secondary_material_buildings=obj[2], a_multiplicity_vector=obj[1])
    return selected, dict(schema='OwnedABMaterialSelectionV2', cohort=group, selected=selected,
        rule='A主指标M不增；同M先减少真实重数向量，再少用第二梯队。第二梯队三个实际第一梯队已满及所有容量/几何/负控/五图检查仍由原流程逐项强制。B只搭配清CLASH，不参与主指标。',
        baseline_nine_pass_source_count=37, baseline_a40_g25_exceptions_explicit=True,
        before=metrics(a0, v0, before), after=metrics(a1, v1, after),
        capacity_checks_replaced=False, geometry_gate_predicates_replaced=False, first_tier_priority_replaced=False,
        formal_acceptance_added=False,
        cannot_support='自有选择顺序不替代真实第一梯队已满、容量、几何、实际负控或五图；不能把三项G25FAIL改成PASS、证明训练适用性或正式验收。')
