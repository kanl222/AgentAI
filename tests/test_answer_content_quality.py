from pathlib import Path

import pytest

from agentai.answering.validation import inspect_answer, relation_values


SOURCE = '```python\n'+Path('examples/optimization-demo/orders.py').read_text(encoding='utf-8')+'\n```'
ROWS = [{'id':'orders','ref':'S1','citation':'document:docs/orders.md#L1-L40','text':SOURCE}]
QUESTION = ('Предложи summarize_orders_positive(orders, customers): итог строго больше нуля. '
            'Покажи два теста: нулевой итог и отрицательный возврат.')


def codes(question, text):
    return {issue['code'] for issue in inspect_answer(question,text,ROWS)}


@pytest.mark.parametrize('reference',[
    'Это показано в таблице 1.', 'См. строку 1.',
    'Смотри таблицу № 1 и строки 1–2.',
])
def test_table_locations_are_not_labelled_values(reference):
    text='SSRS: report_type=1. ИСЛО: report_type=2. '+reference
    assert relation_values(text,['SSRS','ИСЛО'],{'1','2'}) == {'SSRS':{'1'},'ИСЛО':{'2'}}


def test_real_contradiction_is_not_hidden_by_a_table_reference():
    text='SSRS: 1. ИСЛО: 2, но также 1. См. таблицу 1.'
    assert relation_values(text,['SSRS','ИСЛО'],{'1','2'})['ИСЛО']=={'1','2'}


def test_negated_and_explicitly_reported_mistakes_are_not_current_claims():
    text=('ИСЛО: FormController, а не DefaultController. '
          'В предыдущем ответе было ошибочно указано: ИСЛО — DefaultController. '
          'SSRS: DefaultController.')
    assert relation_values(text,['SSRS','ИСЛО'],{'DefaultController','FormController'}) == {
        'SSRS':{'DefaultController'},'ИСЛО':{'FormController'}}


def test_complexity_rejects_a_wrong_total_before_a_correct_total():
    text=('Время выполнения O(N + M). I — общее число позиций. '
          'Общая сложность O(N + M + I + U log U). Память O(M + U).')
    assert 'complexity_conflict' in codes('Какова сложность?',text)
    assert 'complexity_conflict' in codes('Какова сложность?',text.replace('Время выполнения','Временная сложность:'))


def test_global_item_count_cannot_be_charged_again_per_order():
    text=('I — общее число позиций. Вложенный обход требует O(I) на каждый заказ. '
          'Общая сложность O(N + M + I + U log U). Память O(M + U).')
    assert 'complexity_conflict' in codes('Какова сложность?',text)


def test_complexity_requires_memory_without_confusing_phase_costs():
    text=('N — заказы, M — клиенты, I — общее число позиций, U — итоги. '
          'Построение словаря O(M). Суммирование позиций O(I). '
          'Общая сложность O(N + M + I + U log U).')
    assert 'complexity_memory' in codes('Какова сложность?',text)
    assert not codes('Какова сложность?',text+' Дополнительная память O(M + U).')
    assert 'complexity_memory' in codes('Какова сложность?',text+' Память: рассмотрена.')


def fixture_test(prices, name='zero', function='summarize_orders_positive', expected='[]'):
    items=repr([{'price':price,'quantity':1} for price in prices])
    return (f'def test_{name}(self):\n'
            "    customers = [{'id':1,'name':'A'}]\n"
            f"    orders = [{{'customer_id':1,'items':{items}}}]\n"
            f'    self.assertEqual({function}(orders, customers), {expected})\n')


def test_module(*tests):
    return '```python\nimport unittest\nclass Tests(unittest.TestCase):\n'+''.join(
        '    '+line if line.strip() else line for test in tests for line in test.splitlines(True))+'```'
test_module.__test__ = False


def test_zero_name_and_empty_input_are_not_zero_coverage():
    text=test_module(fixture_test(['0.30','-2'],'zero'),fixture_test(['-3'],'negative'))
    assert 'zero_boundary_test' in codes(QUESTION,text)


def test_empty_and_unknown_customer_fixtures_do_not_prove_zero():
    for fixture in ('[]',"[{'customer_id':999,'items':[{'price':'0','quantity':1}]}]"):
        test=fixture_test(['5','-5'])
        lines=test.splitlines(True)
        lines[2]='    orders = '+fixture+'\n'
        assert 'zero_boundary_test' in codes(QUESTION,test_module(''.join(lines)))


def test_result_variable_decimal_inputs_and_pytest_assert_are_recognized():
    text=test_module(fixture_test(['5','-5']),fixture_test(['-3'],'refund'))
    text=text.replace("'price': '5'", "'price': Decimal('5')")
    text=text.replace('self.assertEqual(summarize_orders_positive(orders, customers), [])',
                      'result = summarize_orders_positive(orders, customers)\n        assert result == []')
    assert not codes(QUESTION,text)


def test_actual_zero_and_negative_cases_are_accepted_regardless_of_names():
    text=test_module(fixture_test(['5','-5'],'cancellation'),fixture_test(['-3'],'refund'))
    assert not codes(QUESTION,text)


def test_zero_fixture_must_reach_the_requested_function_and_assert_its_result():
    text=test_module(fixture_test(['5','-5'],'zero','other_function'),fixture_test(['-3'],'negative'))
    assert 'zero_boundary_test' in codes(QUESTION,text)


def test_positive_expected_output_does_not_prove_zero_exclusion():
    text=test_module(fixture_test(['5','-5'],expected="[{'total':0}]"),fixture_test(['-3'],'negative'))
    assert 'zero_boundary_test' in codes(QUESTION,text)


def test_unrecognized_fixture_code_is_never_executed(tmp_path):
    marker=tmp_path/'should-not-exist'
    test=fixture_test(['5','-5']).replace("[{'id':1,'name':'A'}]",f"open({str(marker)!r},'w')")
    assert 'zero_boundary_test' in codes(QUESTION,test_module(test))
    assert not marker.exists()
