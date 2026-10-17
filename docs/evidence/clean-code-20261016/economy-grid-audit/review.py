"""Read-only executable characterization of the grid confirmation projection."""
import ast
from dataclasses import replace
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from rm_client.economy_values import EconomyOffer
from rm_client.ui.economy_presentation import EconomyConfirmationState

source = ast.parse(Path('rm_client/ui/cs2_buy_menu.py').read_text())
function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'build_grid')
start = next(i for i,n in enumerate(function.body) if isinstance(n,ast.Assign)
             and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='enabled')
end = next(i for i,n in enumerate(function.body[start:],start) if isinstance(n,ast.Expr)
           and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='lens')
projection = ast.FunctionDef(name='project',args=ast.arguments(posonlyargs=[],
    args=[ast.arg(arg=n) for n in ('host','selected','pending','now')],kwonlyargs=[],kw_defaults=[],defaults=[]),
    body=function.body[start:end]+[ast.Return(value=ast.Tuple(elts=[ast.Name(id=n,ctx=ast.Load())
        for n in ('text','enabled','action','hold')],ctx=ast.Load()))],decorator_list=[])
from rm_client.ui.economy_interaction import HOLD_SECONDS
namespace={"HOLD_SECONDS": HOLD_SECONDS}
exec(compile(ast.fix_missing_locations(ast.Module(body=[projection],type_ignores=[])),
             '<exact-grid-projection>','exec'),namespace)
project=namespace['project']
base=EconomyOffer('offer','ammo','title','short','desc','unit',1,30,1,1,True)
count=0; mismatches=0
for exists, enabled, status, pending, confirmation, matching, elapsed in product(
        (False,True),(False,True),(False,True),(False,True),('hold','double'),(False,True),(-1,0,.3,.6,1.5)):
    reads=[]
    class Host:
        def __getattribute__(self,name):
            reads.append(name)
            return {'economy_hold_key':'offer' if matching else '', 'economy_hold_started':100-elapsed,
                    'economy_armed_key':'offer' if matching else '', 'economy_armed_until':100-elapsed}[name]
    selected=replace(base,enabled=enabled,status_only=status,confirmation=confirmation) if exists else None
    got=project(Host(),selected,pending,100)
    value=EconomyConfirmationState(selected,pending,'offer' if matching else '',100-elapsed,
                                   'offer' if matching else '',100-elapsed).present(now=100)
    want=(value.text,value.enabled,value.action,value.progress)
    if pending and not exists:
        assert got==('请求处理中…',False,'economy_confirm',0.0)
        assert want==('请先选择商品',False,'economy_confirm',0.0)
        mismatches+=1
    else:
        assert got==want,(selected,pending,elapsed,got,want)
    expected=[]
    if exists and enabled and not status and not pending:
        if confirmation=='hold':
            expected=['economy_hold_key']+(['economy_hold_started'] if matching else [])
        else:
            expected=['economy_armed_key']+(['economy_armed_until'] if matching else [])
            if matching and elapsed<=0: expected+=['economy_armed_until']
    assert reads==expected,(reads,expected)
    count+=1
print(f'PASS: {count} exact grid projection cases; {mismatches} intentional pending/no-selection text differences; lazy field-read order verified for every case')
print('Render mutations remain adapter-owned: category/selection reconciliation, scroll clamping, hover advancement, hotspot replacement. No production change made.')
