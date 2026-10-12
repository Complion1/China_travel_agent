"""Bounded research correction identities and per-call schema projection."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from .models import ProposedSearchAction
from .context import RequirementContextError


def recovery_prompt(prompt, *, code, recovery_number):
    """Reissue the same evidence with controller-owned, bounded feedback only."""
    from app.llm_runtime import PromptEnvelope
    payload = json.loads(prompt.user_text)
    payload['output_recovery'] = {'code': code, 'attempt': recovery_number,
        'required': 'Regenerate one complete JSON object matching the schema. Include all union discriminator fields. '
                    'Every role/interest/meal update key and each requirement_group_id must occur only once. '
                    'Use only visible candidate and evidence handles; evidence must belong to that candidate. '
                    'Use one requirement group update with scoped evaluated candidates and exceptions. '
                    'Do not repeat unchanged judgments or include reasoning, markdown or commentary.'}
    return PromptEnvelope(system_text=prompt.system_text,
        user_text=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def fingerprint(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def target_fingerprint(objective) -> str:
    # Initial research and supplemental research are transports of the same duty.
    kind = objective.gap_type if objective.kind.value == 'supplemental_gap' else objective.kind.value
    kind = {'must_visit_resolution_missing':'must_visit_resolution',
        'avoid_resolution_missing':'avoid_resolution', 'candidate_details_missing':'details_maturity',
        'candidate_count_shortfall':'candidate_count', 'attraction_count':'candidate_count',
        'restaurant_count':'candidate_count', 'meal_coverage_missing':'meal', 'meal_count_shortfall':'meal',
        'meal_lunch':'meal', 'meal_dinner':'meal', 'interest_coverage_missing':'interest_coverage'}.get(kind,kind)
    if kind in {'must_visit_resolution','avoid_resolution'}:
        return fingerprint({'kind':kind, 'group':objective.requirement_group_id})
    data = objective.model_dump(mode='json')
    data = {key:value for key,value in data.items() if key not in {'objective_id','gap_id','gap_type','candidate_uids','kind'}}
    if kind == 'details_maturity':
        data['candidate_uids'] = sorted(objective.candidate_uids)
    if kind == 'meal':
        data['role'] = 'restaurant'
        data['anchor'] = data['radius_km'] = None
    return fingerprint({'kind':kind, **data})


def evidence_fingerprint(state, objective, history=None) -> str:
    # Hash business observations, not operation IDs, query wording or verdicts.
    # Repeated searches of the same facts cannot erase a rejection history.
    history = history or {}
    target = target_fingerprint(objective)
    indexes = {record.observation_index for record in state.search_coverage.records
        if record.objective_id == objective.objective_id
        or (record.objective_id in history and target_fingerprint(history[record.objective_id]) == target) or (
            objective.requirement_group_id is None and record.objective_kind is objective.kind
            and record.role is objective.role and record.interest is objective.interest
            and record.meal_intent is objective.meal_intent)}
    related = []
    for candidate in state.candidates:
        relevant = (candidate.core.uid in objective.candidate_uids
            and candidate.details.status == 'success') or bool(indexes.intersection(candidate.observation_indexes))
        if relevant:
            details = candidate.details.model_dump(mode='json')
            details.pop('observation_indexes', None)
            details.pop('observation_index', None)
            related.append({'core': candidate.core.model_dump(mode='json'), 'details': details})
    return fingerprint(sorted(related, key=lambda item: item['core']['uid']))


def plan_fingerprint(actions, destination) -> str:
    from .fingerprints import action_fingerprint
    return fingerprint(sorted(action_fingerprint(action, destination) for action in actions))


def resolution_keys(state, objective):
    """Credit a first resolution once; changing verdicts back and forth earns none."""
    target = target_fingerprint(objective)
    keys = set()
    for candidate in state.candidates:
        if objective.requirement_group_id:
            matches = (*candidate.must_matches, *candidate.avoid_matches)
            if any(item.requirement_group_id == objective.requirement_group_id
                    and item.verdict.value in {'confirmed', 'no_match'} for item in matches):
                keys.add(fingerprint((target, candidate.core.uid, 'resolved')))
        elif candidate.state.value == 'eligible':
            if objective.interest and objective.interest in candidate.interests:
                keys.add(fingerprint((target, candidate.core.uid, 'interest')))
            elif objective.meal_intent and objective.meal_intent in candidate.meal_intents:
                keys.add(fingerprint((target, candidate.core.uid, 'meal')))
            elif objective.role and any(role.role is objective.role and role.verdict.value == 'supported' for role in candidate.roles):
                keys.add(fingerprint((target, candidate.core.uid, 'role')))
    return keys


@dataclass(frozen=True, slots=True)
class ActionCorrection:
    target: str
    evidence: str
    plan: str
    rule: str
    attempts: int
    active: bool = True

    def project(self, objective, allowed_actions) -> dict:
        return {'target': self.target, 'rule': self.rule, 'actual': 'rejected_batch',
                'expected': 'valid_batch_starting_with_required_objective',
                'required_objective_id': objective.objective_id,
                'allowed_actions': allowed_actions,
                'evidence_fingerprint': self.evidence,
                'attempts': self.attempts}


def record_rejection(records, state, objective, actions, destination, rule, history=None):
    target = target_fingerprint(objective)
    evidence = evidence_fingerprint(state, objective, history)
    previous = next((item for item in reversed(records) if item.target == target and item.evidence == evidence), None)
    value = ActionCorrection(target, evidence, plan_fingerprint(actions, destination), rule,
                             min(2, (previous.attempts if previous else 0) + 1))
    return tuple(item for item in records if (item.target, item.evidence) != (target, evidence)) + (value,)


def _require_union_discriminators(schema):
    """Make generated union schemas match Pydantic's required tag lookup.

    DTO defaults remain available to Python callers. They cannot supply a tag
    before a discriminated union has selected its branch when decoding JSON.
    """
    schema = copy.deepcopy(schema)

    def resolve(branch):
        seen = set()
        while '$ref' in branch:
            reference = branch['$ref']
            if not reference.startswith('#/') or reference in seen:
                raise ValueError('unsupported discriminator schema reference')
            seen.add(reference)
            branch = schema
            for part in reference[2:].split('/'):
                branch = branch[part.replace('~1', '/').replace('~0', '~')]
        return branch

    def visit(node):
        if isinstance(node, dict):
            discriminator = node.get('discriminator')
            if isinstance(discriminator, dict) and isinstance(discriminator.get('propertyName'), str):
                field = discriminator['propertyName']
                for branch in (*node.get('oneOf', ()), *node.get('anyOf', ())):
                    require_field(branch, field)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    def require_field(branch, field):
        target = resolve(branch)
        if field in target.get('properties', {}):
            required = target.setdefault('required', [])
            if field not in required:
                required.append(field)
            return
        alternatives = (*target.get('oneOf', ()), *target.get('anyOf', ()))
        if not alternatives:
            raise ValueError('discriminator field missing from schema branch')
        # Pydantic represents nested discriminated unions as inline unions;
        # the outer tag belongs to each of their eventual object branches.
        for alternative in alternatives:
            require_field(alternative, field)

    visit(schema)
    return schema


def bound_action_schema(base, objective, allowed_actions, detail_uids):
    """Constrain the first JSON array entry; controller remains the admission owner.

    Business-policy violations are returned to the controller as valid structural
    objects, so it can record evidence and feed the next existing invocation.
    """
    class BoundActionEnvelope(base):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            schema = _require_union_discriminators(super().model_json_schema(*args, **kwargs))
            if objective is None:
                return schema
            actions = schema['$defs']['ProposedActionBatch']['properties']['actions']
            actions['prefixItems'] = [{'allOf': [copy.deepcopy(actions['items']),
                {'type': 'object', 'properties': {'objective_id': {'const': objective.objective_id},
                                                 'ordinal': {'const': 0},
                                                 'action_type': {'enum': list(allowed_actions)}},
                 'required': ['objective_id', 'ordinal', 'action_type']}]}]
            if detail_uids:
                actions['prefixItems'][0]['allOf'].append({
                    'if': {'properties': {'action_type': {'const': 'get_place_details'}}},
                    'then': {'properties': {'uid': {'enum': list(detail_uids)}}}})
            return schema
    return BoundActionEnvelope


def bind_first_named_search(actions, objectives, directory, named_groups):
    """Only the initial explicit-name query is controller-owned; no name inference."""
    by_id = {item.objective_id: item for item in objectives}
    pending = set(named_groups)
    bound = []
    for action in actions:
        objective = by_id.get(action.objective_id)
        if isinstance(action, ProposedSearchAction) and objective is not None:
            group = objective.requirement_group_id
            if group is not None and group not in pending:
                requirement = next((item for item in directory if item.group_id == group), None)
                if requirement is None:
                    raise RequirementContextError('missing confirmed requirement context')
                # Bind each group's first search, even outside the first array entry.
                action = ProposedSearchAction(**{**action.model_dump(mode='python'), 'query': requirement.name})
                pending.add(group)
        bound.append(action)
    return tuple(bound)
