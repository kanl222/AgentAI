"""Recognize literal order fixtures tied to assertions; never execute model code.

Unknown expressions and control flow are unverified. This deliberately narrow
checker covers the documented orders/items/price/quantity example contract.
It does not claim coverage for arbitrary test languages or application domains.
"""
import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


@dataclass
class Invocation:
    orders: object
    customers: object


def literal(node, env, target):
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,int,float,type(None))):
        return node.value
    if isinstance(node,ast.Name):
        return env[node.id]
    if isinstance(node,(ast.List,ast.Tuple)):
        return [literal(item,env,target) for item in node.elts]
    if isinstance(node,ast.Dict):
        return {literal(k,env,target):literal(v,env,target) for k,v in zip(node.keys,node.values)}
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
        value=literal(node.operand,env,target)
        if type(value) not in (int,float,Decimal): raise ValueError('Unknown numeric input')
        return -value if isinstance(node.op,ast.USub) else value
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and not node.keywords:
        if node.func.id=='Decimal' and len(node.args)==1:
            return decimal(literal(node.args[0],env,target))
        if node.func.id==target and len(node.args)==2:
            return Invocation(*(literal(arg,env,target) for arg in node.args))
    raise ValueError('Unverified expression')


def decimal(value):
    if not isinstance(value,(str,int,float,Decimal)) or len(str(value))>80:
        raise ValueError('Unverified number')
    result=Decimal(str(value))
    if not result.is_finite() or abs(result.adjusted())>100:
        raise ValueError('Unbounded number')
    return result


def totals(call):
    known={customer['id'] for customer in call.customers}
    result={}
    for order in call.orders:
        identity=order['customer_id']
        if identity not in known: continue
        total=sum((decimal(item['price'])*decimal(item['quantity']) for item in order['items']),Decimal(0))
        result[identity]=result.get(identity,Decimal(0))+total
    return list(result.values())


def boundary_coverage(modules, target):
    coverage=set()
    for module in modules:
        for function in ast.walk(module):
            if not isinstance(function,ast.FunctionDef) or not function.name.startswith('test_'): continue
            env={}
            for statement in function.body:
                try:
                    if isinstance(statement,ast.Assign):
                        value=literal(statement.value,env,target)
                        for name in statement.targets:
                            if not isinstance(name,ast.Name): raise ValueError('Unknown assignment')
                            env[name.id]=value
                        continue
                    pair=None
                    if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call):
                        call=statement.value
                        if (isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name)
                                and call.func.value.id=='self' and call.func.attr=='assertEqual' and len(call.args)==2):
                            pair=call.args
                    if isinstance(statement,ast.Assert) and isinstance(statement.test,ast.Compare):
                        comparison=statement.test
                        if len(comparison.ops)==1 and isinstance(comparison.ops[0],ast.Eq):
                            pair=[comparison.left,comparison.comparators[0]]
                    if pair:
                        a,b=(literal(node,env,target) for node in pair)
                        if isinstance(b,Invocation): a,b=b,a
                        if isinstance(a,Invocation) and b==[]:
                            values=totals(a)
                            if values and all(value==0 for value in values): coverage.add('zero')
                            if values and all(value<0 for value in values): coverage.add('negative')
                    elif not (isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Constant)):
                        # Stop at branches, mutations, helpers or arbitrary calls:
                        # later assertions cannot rely on our earlier snapshot.
                        break
                except (KeyError,TypeError,ValueError,InvalidOperation,ArithmeticError):
                    break
    return coverage


def inspect_boundaries(question, modules, original):
    if not (re.search(r'тест',question,re.I) and re.search(r'нулев',question,re.I)
            and re.search(r'строго больше нуля',question,re.I)):
        return []
    targets=re.findall(r'\b([A-Za-z_]\w*)\s*\(\s*orders\s*,\s*customers\s*\)',question)
    if not targets or not all("'"+field+"'" in original or '"'+field+'"' in original
                              for field in ('customer_id','items','price','quantity')):
        return []
    coverage=boundary_coverage(modules,targets[0])
    issues=[]
    for boundary, requested, example in (
        ('zero', True, 'один клиент, две позиции +5 и -5 с quantity=1'),
        ('negative', bool(re.search(r'отрицател|возврат',question,re.I)), 'один клиент, позиция -3 с quantity=1'),
    ):
        if requested and boundary not in coverage:
            issues.append((boundary+'_boundary_test',
                'Не подтверждён '+('нулевой' if boundary=='zero' else 'отрицательный')+
                ' вход в тестах. Покажи отдельный тест: '+example+
                '; локальные литеральные orders и customers, затем self.assertEqual('
                +targets[0]+'(orders, customers), []). Название теста и пустой вход не доказывают покрытие. '
                'Это статическая проверка данных, не исполнение тестов.'))
    return issues
