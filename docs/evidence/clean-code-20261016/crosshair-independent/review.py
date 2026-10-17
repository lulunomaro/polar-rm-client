import ast, importlib.util, random, subprocess
from pathlib import Path
from types import SimpleNamespace
from rm_client import crosshair_profile as pure, cs2_crosshair as current
old_source = subprocess.check_output(['git','show','fa2c2b8:rm_client/cs2_crosshair.py'], text=True)
assert old_source == Path('tests/fixtures/crosshair_profile_baseline.py').read_text()
old_tree = ast.parse(old_source)
old_funcs = {n.name: ast.dump(n) for n in old_tree.body if isinstance(n, ast.FunctionDef)}
for path in ('rm_client/crosshair_profile.py', 'rm_client/cs2_crosshair.py'):
    for n in ast.parse(Path(path).read_text()).body:
        if isinstance(n, ast.FunctionDef):
            assert old_funcs[n.name] == ast.dump(n), n.name
spec = importlib.util.spec_from_file_location('frozen', 'tests/fixtures/crosshair_profile_baseline.py')
frozen = importlib.util.module_from_spec(spec); spec.loader.exec_module(frozen)
for name in ('ALPHABET','MAX_BYTES','FIELDS','INTEGERS','BOOLS','FORMATS','DEFAULTS','CVARS','JSON_ALIASES'):
    assert getattr(pure,name) == getattr(frozen,name), name
rng=random.Random(70019)
values=[None,True,False,'',{},[],float('nan'),float('inf'),-1,0,1,9999999999]
keys=list(pure.DEFAULTS)+['unknown']
def outcome(f,*args):
    try: return ('ok',f(*args))
    except Exception as e: return ('error',type(e).__name__,str(e))
for _ in range(3000):
    profile={k:rng.choice(values) for k in rng.sample(keys,rng.randrange(0,6))}
    assert outcome(pure.validate_profile,profile) == outcome(frozen.validate_profile,profile)
    assert outcome(current.apply_profile,None,profile) == outcome(frozen.apply_profile,None,profile)
for version in (current, frozen):
    # All missing target settings must be rejected before any field assignment.
    for missing in (*pure.DEFAULTS, 'Style'):
        settings={k:SimpleNamespace(value='untouched') for k in (*pure.DEFAULTS,'Style') if k!=missing}
        result=outcome(version.apply_profile,SimpleNamespace(get_setting=settings.get),{})
        assert result[0]=='error'
        assert all(x.value=='untouched' for x in settings.values())
print('PASS: exact frozen-source hash; every retained/moved function AST; nine schema constants; 3000 malformed profile pairs and None-target precedence; every missing-setting preflight')
