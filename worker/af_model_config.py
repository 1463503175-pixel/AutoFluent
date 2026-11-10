# -*- coding: utf-8 -*-
"""Strict, pre-allocation model configuration for offline CFF analysis.

Coordinates are physical metres. A configuration is a constraint on measured
geometry, not a source for invented counts, fluxes or pressures. Unsupported
coordinate conventions fail early instead of silently reusing 11.9 geometry.
"""
import json
import math
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def _number(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ConfigurationError('%s must be a finite number (metres)' % name)
    return float(x)


def _range(raw, name):
    if not isinstance(raw, list) or len(raw) != 2:
        raise ConfigurationError('%s must be [minimum, maximum] in metres' % name)
    lo, hi = (_number(v, name) for v in raw)
    if not lo < hi:
        raise ConfigurationError('%s has a missing, reversed or zero-width interval' % name)
    return [lo, hi]


def _names(raw, name):
    if not isinstance(raw, list) or not raw or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise ConfigurationError('%s must be a non-empty list of zone names' % name)
    if len(set(x.strip().lower() for x in raw)) != len(raw):
        raise ConfigurationError('%s repeats the same zone name' % name)
    return [x.strip() for x in raw]


def validate_model_config(raw):
    if not isinstance(raw, dict):
        raise ConfigurationError('Model config must be a JSON object')
    if raw.get('schema') != 'autofluent.geometry_config.v1':
        raise ConfigurationError('Expected schema autofluent.geometry_config.v1')
    if raw.get('units') != 'm':
        raise ConfigurationError('All geometric configuration dimensions must use metres (units=m)')
    axis = raw.get('axis')
    if not isinstance(axis, dict):
        raise ConfigurationError('axis is required')
    # Existing native-face cut machinery is genuinely specialised to this basis.
    # Do not pretend that a differently oriented model is supported yet.
    expected_basis = {'wafer_normal': 'x', 'flow': 'y', 'row': 'z', 'flow_sign': 'negative'}
    for key, value in expected_basis.items():
        if axis.get(key) != value:
            raise ConfigurationError('Unsupported axis.%s=%r; current validated cut basis requires %s. Do not transform flux directions implicitly.'
                                     % (key, axis.get(key), value))
    ports = raw.get('ports')
    if not isinstance(ports, dict):
        raise ConfigurationError('ports is required')
    inlet = _names(ports.get('inlet_zones'), 'ports.inlet_zones')
    outlet = _names(ports.get('outlet_zones'), 'ports.outlet_zones')
    if set(x.lower() for x in inlet) & set(x.lower() for x in outlet):
        raise ConfigurationError('A boundary zone cannot be both inlet and outlet')
    wafer = raw.get('wafer')
    if not isinstance(wafer, dict):
        raise ConfigurationError('wafer is required')
    for key in ('thickness_range_m', 'regular_gap_range_m'):
        bounds = _range(wafer.get(key), 'wafer.' + key)
        if bounds[0] <= 0:
            raise ConfigurationError('wafer.%s must be strictly positive' % key)
    if wafer.get('arrangement') not in ('aligned', 'staggered'):
        raise ConfigurationError('wafer.arrangement must be aligned or staggered')
    layers = raw.get('layers')
    if not isinstance(layers, list) or not (1 <= len(layers) <= 6):
        raise ConfigurationError('layers must contain between one and six independently identified layer regions')
    all_layers = []
    used_layer_names = set()
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict) or not isinstance(layer.get('id'), str) or not layer['id']:
            raise ConfigurationError('layers[%d] requires an id' % i)
        if layer['id'] in used_layer_names:
            raise ConfigurationError('Duplicate layer id: ' + layer['id'])
        used_layer_names.add(layer['id'])
        yr = _range(layer.get('y_range_m'), 'layers[%d].y_range_m' % i)
        if layer.get('wafer_x_range_m') is not None:
            _range(layer['wafer_x_range_m'], 'layers[%d].wafer_x_range_m' % i)
        rows = layer.get('rows')
        if not isinstance(rows, list) or not (1 <= len(rows) <= 2):
            raise ConfigurationError('layers[%d].rows must contain one or two rows' % i)
        bound_rows = []
        names = set()
        for j, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                raise ConfigurationError('layers[%d].rows[%d] needs an id' % (i, j))
            if row['id'] in names:
                raise ConfigurationError('Duplicate row id in layer '+layer['id'])
            names.add(row['id'])
            bounds = _range(row.get('z_range_m'), 'layers[%d].rows[%d].z_range_m' % (i,j))
            bound_rows.append((bounds[0], bounds[1], row['id']))
        bound_rows.sort()
        if len(bound_rows)==2 and bound_rows[0][1]>bound_rows[1][0]:
            split=(raw.get('partition') or {}).get('overlap_split_z_m')
            if (wafer.get('arrangement')!='staggered' or split is None or
                    not bound_rows[1][0]<_number(split,'partition.overlap_split_z_m')<bound_rows[0][1]):
                raise ConfigurationError('Overlapping Z rows require staggered geometry and a split inside the overlap')
        cut = layer.get('cut_y_m')
        if cut is not None and not yr[0] < _number(cut, layer['id']+'.cut_y_m') < yr[1]:
            raise ConfigurationError('Layer cut_y_m must be inside layer y_range_m')
        all_layers.append((yr[0], yr[1], layer['id']))
    all_layers.sort()
    if any(left[1] > right[0] for left, right in zip(all_layers, all_layers[1:])):
        raise ConfigurationError('Overlapping layer Y ranges are ambiguous')
    partition = raw.get('partition', {})
    if not isinstance(partition, dict):
        raise ConfigurationError('partition must be an object')
    if partition.get('central_bypass','auto') not in ('auto','present','absent'):
        raise ConfigurationError('partition.central_bypass must be auto/present/absent')
    if partition.get('overlap_split_z_m') is not None:
        _number(partition['overlap_split_z_m'], 'partition.overlap_split_z_m')
    expected = raw.get('expected', {})
    if not isinstance(expected, dict):
        raise ConfigurationError('expected must be an object')
    for name in ('wafer_count','channel_count','baffle_hole_count'):
        val=expected.get(name)
        if val is not None and (type(val) is not int or val < 0):
            raise ConfigurationError('expected.%s must be a nonnegative integer or null' % name)
    measurement=raw.get('measurement',{})
    if not isinstance(measurement,dict):
        raise ConfigurationError('measurement must be an object')
    if measurement.get('baffle_y_range_m') is not None:
        _range(measurement['baffle_y_range_m'],'measurement.baffle_y_range_m')
    stations=measurement.get('pressure_stations',[])
    if not isinstance(stations,list):
        raise ConfigurationError('measurement.pressure_stations must be an array')
    for i,station in enumerate(stations):
        if not isinstance(station,dict) or not isinstance(station.get('id'),str):
            raise ConfigurationError('pressure_stations[%d] needs a string id' % i)
        _number(station.get('y_m'),'pressure_stations[%d].y_m'%i)
        thickness=_number(station.get('thickness_m'),'pressure_stations[%d].thickness_m'%i)
        if thickness<=0:
            raise ConfigurationError('Pressure station thickness must be positive')
    resource=raw.get('resources',{})
    if not isinstance(resource,dict):
        raise ConfigurationError('resources must be an object')
    limit=resource.get('memory_budget_mb',6144)
    if type(limit) is not int or not 512<=limit<=16384:
        raise ConfigurationError('resources.memory_budget_mb must be 512..16384 MiB')
    # JSON serialization checks disallow NaN and extra exotic values.
    json.dumps(raw,allow_nan=False)
    return raw


def load_model_config(path):
    try:
        with Path(path).expanduser().open('r',encoding='utf-8') as fp:
            raw=json.load(fp)
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:
        raise ConfigurationError('Unable to load model configuration: '+str(exc)) from exc
    return validate_model_config(raw)
