import unittest
from copy import deepcopy
from decimal import Decimal
from orders import summarize_orders


class OrdersTests(unittest.TestCase):
    def test_aggregation_sort_and_unknown_customer(self):
        customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
        orders = [
            {'customer_id': 1, 'items': [{'price': '0.10', 'quantity': 3}]},
            {'customer_id': 2, 'items': [{'price': '5.00', 'quantity': 2}]},
            {'customer_id': 1, 'items': [{'price': '2.00', 'quantity': 1}]},
            {'customer_id': 999, 'items': [{'price': '99', 'quantity': 1}]},
        ]
        self.assertEqual(summarize_orders(orders, customers), [
            {'customer_id': 2, 'name': 'Beta', 'total': Decimal('10.00')},
            {'customer_id': 1, 'name': 'Alpha', 'total': Decimal('2.30')},
        ])

    def test_empty(self):
        self.assertEqual(summarize_orders([], []), [])

    def test_threshold_after_aggregation_inclusive_and_input_unchanged(self):
        customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
        orders = [{'customer_id': 1, 'items': [{'price': '6', 'quantity': 1}]},
                  {'customer_id': 1, 'items': [{'price': '4', 'quantity': 1}]},
                  {'customer_id': 2, 'items': [{'price': '9', 'quantity': 1}]}]
        before = deepcopy((orders, customers))
        self.assertEqual(summarize_orders(orders, customers, Decimal('10')),
                         [{'customer_id': 1, 'name': 'Alpha', 'total': Decimal('10')}])
        self.assertEqual((orders, customers), before)

    def test_ties_follow_first_order_not_customer_list(self):
        customers = [{'id': 1, 'name': 'Alpha'}, {'id': 2, 'name': 'Beta'}]
        orders = [{'customer_id': 2, 'items': [{'price': '2', 'quantity': 1}]},
                  {'customer_id': 1, 'items': [{'price': '2', 'quantity': 1}]}]
        self.assertEqual([r['customer_id'] for r in summarize_orders(orders, customers)], [2, 1])

    def test_refunds_and_duplicate_customers(self):
        customers = [{'id': 1, 'name': 'First'}, {'id': 1, 'name': 'Duplicate'}]
        orders = [{'customer_id': 1, 'items': [{'price': '-3', 'quantity': 1}]}]
        self.assertEqual(summarize_orders(orders, customers),
                         [{'customer_id': 1, 'name': 'First', 'total': Decimal('-3')}])
        self.assertEqual(summarize_orders(orders, customers, Decimal('0')), [])


if __name__ == '__main__':
    unittest.main()
