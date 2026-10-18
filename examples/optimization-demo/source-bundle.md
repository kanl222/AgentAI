# OPTIMIZATION_DEMO_ORDERS — исходники для анализа

# Тестовый проект OPTIMIZATION_DEMO_ORDERS

Учебный код расчёта сумм заказов. Все данные искусственные. Проверенная версия
использует словари и одну сортировку, сохраняя Decimal и входные данные.

Задание агенту: проверь summarize_orders в orders.py: Decimal, пропуск неизвестных
клиентов, порядок по убыванию суммы и порядок первого появления клиента в заказах
при равных суммах. Оцени сложность и предложи отдельное улучшение с тестами.

min_total=None сохраняет отрицательные суммы возвратов при вызове без порога.
Явный порог применяется к итогам клиента включительно, после накопления.
Тесты проверяют порог, равные суммы, имена, дубликаты ID и неизменность входа.

При анализе указывать файл и исходные номера строк. Сначала предложить патч
и способ проверки. Загруженная копия не предоставляет агенту права записи
в локальные файлы: применение изменений выполняется отдельно.

Проверка: python -m unittest discover -s examples/optimization-demo -v


## orders.py
```text
001: """Aggregate synthetic orders using exact decimal arithmetic."""
002: from decimal import Decimal
003: 
004: 
005: def summarize_orders(orders, customers, min_total=None):
006:     """Sort totals descending; ties follow first encountered customer order.
007: 
008:     None keeps all totals, including negative refunds. An explicit threshold
009:     is inclusive and applies to aggregated customer totals, not single orders.
010:     Duplicate customer IDs use the first customer, matching the original lookup.
011:     """
012:     customer_map = {}
013:     for customer in customers:
014:         customer_map.setdefault(customer['id'], customer)
015:     totals = {}
016:     for order in orders:
017:         customer = customer_map.get(order['customer_id'])
018:         if customer is None:
019:             continue
020:         total = sum((Decimal(str(item['price'])) * item['quantity'] for item in order['items']), Decimal('0'))
021:         customer_id = customer['id']
022:         totals[customer_id] = totals.get(customer_id, Decimal('0')) + total
023:     result = [
024:         {'customer_id': customer_id, 'name': customer_map[customer_id]['name'], 'total': total}
025:         for customer_id, total in totals.items()
026:         if min_total is None or total >= min_total
027:     ]
028:     return sorted(result, key=lambda row: row['total'], reverse=True)
```


## test_orders.py
```text
001: import unittest
002: from copy import deepcopy
003: from decimal import Decimal
004: from orders import summarize_orders
005: 
006: 
007: class OrdersTests(unittest.TestCase):
008:     def test_aggregation_sort_and_unknown_customer(self):
009:         customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
010:         orders = [
011:             {'customer_id': 1, 'items': [{'price': '0.10', 'quantity': 3}]},
012:             {'customer_id': 2, 'items': [{'price': '5.00', 'quantity': 2}]},
013:             {'customer_id': 1, 'items': [{'price': '2.00', 'quantity': 1}]},
014:             {'customer_id': 999, 'items': [{'price': '99', 'quantity': 1}]},
015:         ]
016:         self.assertEqual(summarize_orders(orders, customers), [
017:             {'customer_id': 2, 'name': 'Beta', 'total': Decimal('10.00')},
018:             {'customer_id': 1, 'name': 'Alpha', 'total': Decimal('2.30')},
019:         ])
020: 
021:     def test_empty(self):
022:         self.assertEqual(summarize_orders([], []), [])
023: 
024:     def test_threshold_after_aggregation_inclusive_and_input_unchanged(self):
025:         customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
026:         orders = [{'customer_id': 1, 'items': [{'price': '6', 'quantity': 1}]},
027:                   {'customer_id': 1, 'items': [{'price': '4', 'quantity': 1}]},
028:                   {'customer_id': 2, 'items': [{'price': '9', 'quantity': 1}]}]
029:         before = deepcopy((orders, customers))
030:         self.assertEqual(summarize_orders(orders, customers, Decimal('10')),
031:                          [{'customer_id': 1, 'name': 'Alpha', 'total': Decimal('10')}])
032:         self.assertEqual((orders, customers), before)
033: 
034:     def test_ties_follow_first_order_not_customer_list(self):
035:         customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
036:         orders = [{'customer_id': 2, 'items': [{'price': '2', 'quantity': 1}]},
037:                   {'customer_id': 1, 'items': [{'price': '2', 'quantity': 1}]}]
038:         self.assertEqual([r['customer_id'] for r in summarize_orders(orders, customers)], [2, 1])
039: 
040:     def test_refunds_and_duplicate_customers(self):
041:         customers = [{'id': 1, 'name': 'First'}, {'id': 1, 'name': 'Duplicate'}]
042:         orders = [{'customer_id': 1, 'items': [{'price': '-3', 'quantity': 1}]}]
043:         self.assertEqual(summarize_orders(orders, customers),
044:                          [{'customer_id': 1, 'name': 'First', 'total': Decimal('-3')}])
045:         self.assertEqual(summarize_orders(orders, customers, Decimal('0')), [])
046: 
047: 
048: if __name__ == '__main__':
049:     unittest.main()
```
