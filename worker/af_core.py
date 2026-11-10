"""AutoFluent safe solver state adapter. No case/data write, initialize, or iterate.

The API is intentionally narrow.  Unknown settings and boundary types fail closed.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SCHEMA = "autofluent.conditions.v1"
# Whitelisted paths backed by PyFluent settings; presence must still be confirmed live.
CORE_PATHS = [
    "setup.general.solver.type",
    "setup.general.solver.time",
    "setup.general.operating_conditions.operating_pressure",
    "setup.models.viscous.model",
    "setup.models.energy.enabled",
    "solution.methods.p_v_coupling.flow_scheme",
    "solution.methods.gradient_scheme",
    "solution.methods.spatial_discretization.pressure",
    "solution.methods.spatial_discretization.momentum",
    "solution.methods.spatial_discretization.energy",
]
ZONE_PATHS = {
    "mass_flow_inlet": ["momentum.mass_flow_specification", "momentum.mass_flow_rate", "thermal.temperature"],
    "pressure_outlet": ["momentum.gauge_pressure", "thermal.backflow_total_temperature"],
    "velocity_inlet": ["momentum.velocity", "momentum.velocity_magnitude", "thermal.temperature"],
    "wall": ["thermal.thermal_condition", "thermal.temperature"],
}
# include others in inventory, but no changing unsupported types
INVENTORY_TYPES = [
    "mass_flow_inlet", "pressure_outlet", "velocity_inlet", "pressure_inlet",
    "mass_flow_outlet", "wall", "symmetry", "outflow", "axis", "periodic", "interface", "interior"
]

class AFError(Exception):
    pass

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def convert(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): convert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [convert(v) for v in value]
    return str(value)

def get_child(node, path: str):
    for p in path.split("."):
        if not p.isidentifier() or p.startswith("_"):
            raise AFError("非法设置路径")
        node = getattr(node, p)
    return node

def read(node):
    return convert(node.get_state())

def eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, abs_tol=1e-10, rel_tol=1e-8)
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return all(eq(a[k], b[k]) for k in a)
    return a == b

def identify_zone_collection(settings, zone_type):
    return getattr(settings.setup.boundary_conditions, zone_type)

def object_names(collection):
    try:
        result = collection.get_object_names()
    except (AttributeError, TypeError):
        result = collection.keys()
    return sorted(str(x) for x in result)

def _field_item(path, node):
    try:
        return {"path": path, "value": read(get_child(node, path)), "state": "read"}
    except Exception as e:
        return {"path": path, "state": "unavailable", "reason": str(e)[:240]}

def scan(settings) -> dict:
    fields = [_field_item(p, settings) for p in CORE_PATHS]
    boundaries = []
    for typ in INVENTORY_TYPES:
        try:
            collection = identify_zone_collection(settings, typ)
            names = object_names(collection)
        except Exception:
            continue
        for name in names:
            z = {"name": name, "type": typ, "fields": []}
            for path in ZONE_PATHS.get(typ, []):
                z["fields"].append(_field_item(path, collection[name]))
            boundaries.append(z)
    boundaries.sort(key=lambda b: (b['type'], b['name']))
    materials = []
    try:
        fluid = settings.setup.cell_zone_conditions.fluid
        for name in object_names(fluid):
            mat_field = _field_item("material", fluid[name])
            material_properties = None
            if mat_field["state"] == "read" and isinstance(mat_field["value"], str):
                try:
                    mat_obj = settings.setup.materials.fluid[mat_field["value"]]
                    material_properties = convert(mat_obj.get_state())
                except Exception:
                    pass
            materials.append({"name": name, "material": mat_field, "material_properties": material_properties})
    except Exception:
        pass
    fingerprint = hashlib.sha256(json.dumps([(z['type'],z['name']) for z in boundaries], ensure_ascii=False).encode()).hexdigest()
    essential_read={f['path'] for f in fields if f['state']=='read'}
    essential={'setup.general.solver.type','setup.general.solver.time',
               'setup.general.operating_conditions.operating_pressure',
               'setup.models.viscous.model','setup.models.energy.enabled',
               'solution.methods.p_v_coupling.flow_scheme'}
    readiness='ready_to_review' if essential.issubset(essential_read) and boundaries and materials else 'incomplete'
    return {"schema": "autofluent.snapshot.v1", "read_at": utc_now(), "fields": fields,
            "scan_readiness":readiness,"missing_essential":sorted(essential-essential_read),
            "coverage_note":"仅读取受支持白名单字段；不是完整物理工况一致性验证",
            "boundaries": boundaries, "fluid_zones": materials, "boundary_signature": fingerprint,
            "fingerprint_scope": "boundary-type-and-name-only", "warnings": ["边界名称签名不等于几何指纹；几何变化时需要重新审核。"]}

def export_template(snapshot, template_id="ALD-EXTRACTED") -> dict:
    fields = [{"path": f["path"], "value": f["value"], "required": True}
              for f in snapshot["fields"] if f["state"] == "read"]
    zones = []
    for z in snapshot["boundaries"]:
        if z["type"] not in ZONE_PATHS:
            continue
        valid = [{"path": f["path"], "value": f["value"], "required": True}
                 for f in z["fields"] if f["state"] == "read"]
        if valid:
            zones.append({"role": z["name"], "type": z["type"], "zone": z["name"], "fields": valid})
    fluids = [{"role":z['name'], "zone":z['name'], "material":z['material']['value'],
               "material_properties": z.get('material_properties')}
              for z in snapshot.get('fluid_zones',[]) if z['material']['state']=='read']
    return {"schema": SCHEMA, "template_id": template_id,
            "extracted_at": snapshot['read_at'], "source_boundary_signature": snapshot['boundary_signature'],
            "verified_with_real_case": False,
            "fields": fields, "zones": zones, "fluid_zones": fluids,
            "notes": "从当前 Fluent Case 读取；必须先核对入口流量、材料、温度和其他未覆盖字段，再批准发布。"}

def check_template(template):
    if template.get("schema") != SCHEMA: raise AFError("工况模板 schema 错误")
    if not isinstance(template.get('fields'),list) or not isinstance(template.get('zones'),list):
        raise AFError("模板缺少 fields 或 zones")
    if not template.get('template_id'): raise AFError('缺少模板 ID')
    seen = set()
    for item in template['fields']:
        p = item.get('path')
        if p not in CORE_PATHS: raise AFError(f"不允许发布路径: {p}")
        if p in seen: raise AFError(f"重复路径: {p}")
        if 'value' not in item: raise AFError(f"缺少值: {p}")
        seen.add(p)
    roles = set()
    zone_targets = set()
    for zone in template['zones']:
        typ=zone.get('type'); name=zone.get('zone'); role=zone.get('role')
        if typ not in ZONE_PATHS: raise AFError(f"不支持写入该边界类型: {typ}")
        if not isinstance(name, str) or not name or not role: raise AFError('边界 role/zone 为空')
        if role in roles: raise AFError(f'重复边界角色: {role}')
        roles.add(role)
        if (typ,name) in zone_targets: raise AFError(f'重复绑定同一目标边界: {name}')
        zone_targets.add((typ,name))
        seen_zone_fields = set()
        for item in zone.get('fields', []):
            if item.get('path') not in ZONE_PATHS[typ]:
                raise AFError(f"不允许发布边界属性 {typ}: {item.get('path')}")
            if 'value' not in item: raise AFError('边界缺少目标值')
            if item['path'] in seen_zone_fields: raise AFError('边界字段重复')
            seen_zone_fields.add(item['path'])
    fluid_targets=set()
    for fluid in template.get('fluid_zones',[]):
        if fluid.get('zone') in fluid_targets: raise AFError('重复绑定流体域')
        fluid_targets.add(fluid.get('zone'))
        if not fluid.get('zone') or not fluid.get('material'):
            raise AFError('材料与流体域信息不完整')

def _plan_signature(template, items, boundary_signature):
    """Stable, data-dependent fingerprint; excludes mutable timestamps.

    It intentionally proves a settings snapshot, NOT the underlying geometric identity.
    """
    obj = {"template": template, "items": items, "boundary_signature": boundary_signature}
    raw = json.dumps(convert(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def make_plan(settings, template):
    check_template(template)
    current = scan(settings)
    ztypes = {(z['type'], z['name']) for z in current['boundaries']}
    fluid_names = {z['name'] for z in current['fluid_zones']}
    # All real active flux boundaries need a role in the template; otherwise a
    # newly introduced inlet/outlet can silently violate nominally identical BCs.
    flow_types = {'mass_flow_inlet','velocity_inlet','pressure_inlet','pressure_outlet','mass_flow_outlet','outflow'}
    mapped = {(z['type'], z['zone']) for z in template['zones']}
    unmapped = sorted(ztypes.intersection({(z['type'],z['name']) for z in current['boundaries'] if z['type'] in flow_types}) - mapped)
    items=[]
    for typ,name in unmapped:
        items.append({'path':f'boundary:{typ}:{name}', 'status':'blocked', 'required':True,
                      'reason':'当前模型存在未纳入工况模板的进出口边界'})
    for field in template['fields']:
        p=field['path']
        items.append(_compare(p, field['value'], lambda p=p: get_child(settings,p), field.get('required',True)))
    for zone in template['zones']:
        prefix=f"boundary:{zone['type']}:{zone['role']} -> {zone['zone']}"
        if (zone['type'],zone['zone']) not in ztypes:
            items.append({'path':prefix, 'status':'blocked', 'reason':'目标边界不存在或类型不匹配', 'required':True})
            continue
        obj=identify_zone_collection(settings,zone['type'])[zone['zone']]
        for field in zone.get('fields',[]):
            p=field['path']; items.append(_compare(prefix+'.'+p,field['value'],lambda p=p,obj=obj:get_child(obj,p),field.get('required',True)))
    for f in template.get('fluid_zones',[]):
        prefix=f"fluid:{f['role']} -> {f['zone']}"
        if f['zone'] not in fluid_names:
            items.append({'path':prefix,'status':'blocked','reason':'流体域不存在','required':True}); continue
        obj=settings.setup.cell_zone_conditions.fluid[f['zone']]
        items.append(_compare(prefix+'.material',f['material'],lambda obj=obj:obj.material,True))
        if f.get('material_properties') is not None:
            # Material definitions are compared but never silently overwritten.
            material=f['material']
            try:
                actual=convert(settings.setup.materials.fluid[material].get_state())
                if not eq(actual,f['material_properties']):
                    items.append({'path':prefix+'.material_properties','status':'blocked', 'required':True,
                                  'reason':'材料物性定义与模板不同；必须单独在 Fluent 中确认'})
            except Exception as e:
                items.append({'path':prefix+'.material_properties','status':'blocked', 'required':True,
                              'reason':'无法核验材料物性：'+str(e)[:120]})
    present={x['path'] for x in template['fields']}
    essentials={'setup.general.solver.type','setup.general.solver.time',
                'setup.general.operating_conditions.operating_pressure',
                'setup.models.viscous.model','setup.models.energy.enabled',
                'solution.methods.p_v_coupling.flow_scheme'}
    for path in sorted(essentials-present):
        items.append({'path':path,'status':'blocked','required':True,'reason':'必需设置未纳入模板'})
    if not any(z['type'] in ('mass_flow_inlet','velocity_inlet') for z in template['zones']):
        items.append({'path':'inlet_binding','status':'blocked','required':True,'reason':'无受支持入口边界绑定'})
    if not any(z['type']=='pressure_outlet' for z in template['zones']):
        items.append({'path':'outlet_binding','status':'blocked','required':True,'reason':'无压力出口边界绑定'})
    if not template.get('fluid_zones'):
        items.append({'path':'fluid_material','status':'blocked','required':True,'reason':'没有流体域材料绑定'})
    blockers=[x for x in items if x['status']=='blocked' and x.get('required',True)]
    return {"template_id":template['template_id'], "snapshot_signature":current['boundary_signature'],
            "plan_signature":_plan_signature(template, items, current['boundary_signature']),
            "signature_scope":"readable-settings-and-boundary-names-only; not a mesh fingerprint",
            "items":items, "blocker_count":len(blockers), "change_count":sum(x['status']=='change' for x in items),
            "ok":not blockers, "read_at":utc_now()}

def _compare(label,target,getter,required):
    try:
        existing=read(getter())
        return {'path':label,'before':existing,'target':target,'status':'same' if eq(existing,target) else 'change','required':bool(required)}
    except Exception as e:
        return {'path':label,'target':target,'status':'blocked','reason':str(e)[:240], 'required':bool(required)}

def _resolve(settings, template, label):
    for f in template['fields']:
        if label==f['path']:
            return get_child(settings,f['path'])
    for z in template['zones']:
        prefix=f"boundary:{z['type']}:{z['role']} -> {z['zone']}"
        for f in z.get('fields',[]):
            if label==prefix+'.'+f['path']:
                return get_child(identify_zone_collection(settings,z['type'])[z['zone']],f['path'])
    for f in template.get('fluid_zones',[]):
        if label==f"fluid:{f['role']} -> {f['zone']}.material":
            return settings.setup.cell_zone_conditions.fluid[f['zone']].material
    raise AFError('路径不在模板白名单')

def publish(settings, template, confirm, expected_plan_signature=None):
    if confirm != '发布工况': raise AFError('发布需要输入「发布工况」')
    if template.get('verified_with_real_case') is not True:
        raise AFError('模板未由用户核对并批准，不允许发布')
    if not isinstance(expected_plan_signature, str) or len(expected_plan_signature) != 64:
        raise AFError('必须先运行发布前差异检查，并使用本次预检签名发布')
    plan=make_plan(settings,template)
    if plan['plan_signature'] != expected_plan_signature:
        raise AFError('发布被阻断：当前 Fluent 设置、边界或模板与刚才预检不一致，必须重新预检')
    # V1 cannot promise a complete standard condition from a partial template.
    present={x['path'] for x in template['fields']}
    essentials={'setup.general.solver.type','setup.general.solver.time',
                'setup.general.operating_conditions.operating_pressure',
                'setup.models.viscous.model','setup.models.energy.enabled',
                'solution.methods.p_v_coupling.flow_scheme'}
    if not essentials.issubset(present):
        raise AFError('模板缺少必要的全局物理或求解设置；发布被阻断')
    if not any(z['type'] in ('mass_flow_inlet','velocity_inlet') for z in template['zones']):
        raise AFError('模板没有受支持的入口边界；发布被阻断')
    if not any(z['type']=='pressure_outlet' for z in template['zones']):
        raise AFError('模板没有受支持的压力出口；发布被阻断')
    if not template.get('fluid_zones'):
        raise AFError('模板未包括流体域材料；发布被阻断')
    if not plan['ok']: raise AFError(f"预检失败：{plan['blocker_count']} 项阻断；没有写入任何设置。")
    changed=[]
    try:
        for item in plan['items']:
            if item['status'] != 'change': continue
            node=_resolve(settings,template,item['path'])
            previous=copy.deepcopy(read(node))
            # record BEFORE attempting to write, to enable rollback if setter partially writes
            changed.append((item['path'], node, previous))
            node.set_state(item['target'])
            after=read(node)
            if not eq(after,item['target']):
                raise AFError('发布后回读不一致: '+item['path'])
        verified=make_plan(settings,template)
        if not verified['ok'] or verified['change_count']:
            raise AFError('最终核验未全部通过，尝试恢复原值')
        return {'status':'verified_partial_scope','changed':len(changed),'verified_items':sum(x['status']=='same' for x in verified['items']),
                'optional_unavailable':sum(x['status']=='blocked' for x in verified['items']),
                'verified_at':utc_now(),'plan':verified,
                'warning':'只证明模板所列可读取字段一致；不代表完整工况一致。未保存 Case，也未初始化或求解。'}
    except Exception as exc:
        rollback=[]
        for path,node,old in reversed(changed):
            try:
                node.set_state(old)
                rollback.append({'path':path,'result':'restored' if eq(read(node),old) else 'mismatch'})
            except Exception as err:
                rollback.append({'path':path,'result':'failed','reason':str(err)[:180]})
        raise AFError(json.dumps({'failure':str(exc),'rollback':rollback,
            'warning':'请在 Fluent 中核验实际设置；部分设置的副作用可能无法自动恢复。'},ensure_ascii=False)) from exc
