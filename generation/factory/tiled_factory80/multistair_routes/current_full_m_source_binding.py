"""OWN metadata dispatch. Original structural count comes from bound raw author.

Density receipts intentionally lack stair_count; do not alter those receipts or
invent a count. Their unchanged producer binder proves structural lineage.
"""
import sys
from multistair_cpu_contract import require, check_structural_scope


def bind_existing_support(m, row, main):
    sys.path.insert(0, str(m.A1DIR))
    from owned_current_support_binding import bind_density_support
    consumer = m.checked(row['existing_consumer_ref'])
    source = m.read(consumer['path'])['rows'][0]
    require(all(source[k] == row[k] for k in
                ('building', 'attempt', 'root_ref', 'authoring_ref', 'raw_authoring_ref')),
            'Exact genuine current support/author/root/attempt binding required')
    bound = bind_density_support(source, m.C)
    author, raw = bound['density_authoring'], bound['raw_authoring']
    check_structural_scope(author, raw, main)
    proof = m.unique([consumer, *bound['ordered_protected_refs']])
    return author, raw, proof, bound['producer']
