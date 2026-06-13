filepath = '/Users/sasakikousei/Projects/Clio-Agent/run.py'
with open(filepath, 'r') as f:
    content = f.read()

old = '    # Check for --setting flag to force reconfiguration\n    force_reconfigure = "--setting" in sys.argv\n    \n    # Model selection - only prompt if not using --no-prompt flag, or if --setting is used'

new = '    # Check for --setting flag to force reconfiguration\n    force_reconfigure = "--setting" in sys.argv\n\n    # When --setting is used, reset config.yaml to a clean state first\n    if force_reconfigure:\n        _reset_config_yaml()\n    \n    # Model selection - only prompt if not using --no-prompt flag, or if --setting is used'

if old not in content:
    print('ERROR: old string not found')
    # Debug: show the area
    idx = content.find('force_reconfigure')
    if idx >= 0:
        print(repr(content[idx-50:idx+200]))
else:
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print('Patched successfully')
