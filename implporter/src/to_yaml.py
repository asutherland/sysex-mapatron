#!/usr/bin/env python
"""Use this if you want to represent jupx.json as a YAML
with hex numbers for readability"""
import yaml

def hex_representer(dumper, data):
    return dumper.represent_int(hex(data))


yaml.add_representer(int, hex_representer, Dumper=yaml.CDumper)

def to_nice_yaml(in_json, out_yaml):
    data = None
    with open(in_json, 'rb') as fhin:
        data = yaml.load(fhin, Loader=yaml.CLoader)
    with open(out_yaml, 'w', encoding='utf-8') as fhout:
        yaml.dump(data, stream=fhout, Dumper=yaml.CDumper, sort_keys=False, indent=2)

if __name__ == '__main__':
  to_nice_yaml('../sysex-maps/jupx.json', '../sysex-maps/jupx.yaml')
