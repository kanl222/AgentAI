"""Aggregate synthetic orders using exact decimal arithmetic."""
from decimal import Decimal


def summarize_orders(orders, customers, min_total=None):
    """Sort totals descending; ties follow first encountered customer order.

    None keeps all totals, including negative refunds. An explicit threshold
    is inclusive and applies to aggregated customer totals, not single orders.
    Duplicate customer IDs use the first customer, matching the original lookup.
    """
    customer_map = {}
    for customer in customers:
        customer_map.setdefault(customer['id'], customer)
    totals = {}
    for order in orders:
        customer = customer_map.get(order['customer_id'])
        if customer is None:
            continue
        total = sum((Decimal(str(item['price'])) * item['quantity'] for item in order['items']), Decimal('0'))
        customer_id = customer['id']
        totals[customer_id] = totals.get(customer_id, Decimal('0')) + total
    result = [
        {'customer_id': customer_id, 'name': customer_map[customer_id]['name'], 'total': total}
        for customer_id, total in totals.items()
        if min_total is None or total >= min_total
    ]
    return sorted(result, key=lambda row: row['total'], reverse=True)
