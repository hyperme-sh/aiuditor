"""Preserved Panopticon schema projection; test-only, never imports other owners."""
from __future__ import annotations

import ast
import copy
import importlib
import hashlib
import inspect
import textwrap

def _dereference(schema, definitions):
    if '$ref' in schema:
        return definitions[schema['$ref'].split('/')[-1]]
    if 'anyOf' in schema:
        return _dereference(next(s for s in schema['anyOf'] if s.get('type') != 'null'), definitions)
    return schema


def _field_path(expression):
    paths = []
    for node in ast.walk(expression):
        if isinstance(node, ast.Attribute):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.insert(0, node.attr)
                node = node.value
            if isinstance(node, ast.Name) and node.id == 'self':
                while parts and parts[-1] in ('value', 'isoformat', 'model_dump'):
                    parts.pop()
                if parts:
                    paths.append(parts)
    if not paths:
        raise ValueError(f'unsupported serializer expression: {ast.unparse(expression)}')
    return max(paths, key=len)


def generated_schema(event_type, source):
    if 'inline' in source:
        schema = copy.deepcopy(source['inline'])
    else:
        model = getattr(importlib.import_module(source['module']), source['model'])
        schema = model.model_json_schema(mode='serialization')
        if source.get('method'):
            method = ast.parse(textwrap.dedent(inspect.getsource(getattr(model, source['method']))))
            returned = next(n.value for n in ast.walk(method) if isinstance(n, ast.Return))
            if isinstance(returned, ast.Dict):
                properties = {}
                for key, value in zip(returned.keys, returned.values):
                    if isinstance(value, ast.Constant):
                        field_schema = {'const':value.value}
                    else:
                        field_schema = schema
                        for part in _field_path(value):
                            field_schema = _dereference(field_schema, schema.get('$defs', {}))['properties'][part]
                    properties[key.value] = copy.deepcopy(field_schema)
                schema = {'type':'object', 'properties':properties, 'required':list(properties),
                          '$defs':schema.get('$defs', {}), 'additionalProperties':False}
            else:
                if not (isinstance(returned, ast.Call) and isinstance(returned.func, ast.Attribute)
                        and returned.func.attr == 'model_dump'):
                    raise ValueError('unsupported event serializer')
            # Source bodies are part of the wire contract, even when shape stays the same.
            schema['x-serializer-sha256'] = hashlib.sha256(ast.dump(returned, include_attributes=False).encode()).hexdigest()
        schema['additionalProperties'] = False
        schema.get('properties', {}).update(source.get('optional_properties', {}))
        needed = set()
        def refs(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    name = node['$ref'].split('/')[-1]
                    if name not in needed:
                        needed.add(name)
                        refs(schema['$defs'][name])
                for key, value in node.items():
                    if key != '$defs': refs(value)
            elif isinstance(node, list):
                for value in node: refs(value)
        refs(schema)
        if needed:
            schema['$defs'] = {name:schema['$defs'][name] for name in sorted(needed)}
        else:
            schema.pop('$defs', None)
    schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
                   '$id':f'urn:controlplane:event:{event_type}',
                   'x-event-type':event_type, 'x-topic':'controlplane.events.v1',
                   'x-owner':source['owner']})
    return schema

