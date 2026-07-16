package com.example.storefront.orders;

import java.util.List;

import com.example.storefront.notifications.Notifier;

/**
 * Creates, ships, and cancels customer orders.
 */
public class OrderService {

    private final OrderRepository repository;
    private final Notifier notifier;

    public OrderService(OrderRepository repository, Notifier notifier) {
        this.repository = repository;
        this.notifier = notifier;
    }

    /**
     * Create a new order for the given customer and line items.
     */
    public Order createOrder(String customerId, List<LineItem> items) {
        if (items.isEmpty()) {
            throw new IllegalArgumentException("order must contain at least one item");
        }
        Order order = new Order(customerId, items, OrderStatus.PENDING);
        repository.save(order);
        notifier.notifyOrderCreated(order);
        return order;
    }

    /**
     * Mark an order as shipped, notifying the customer.
     */
    public void shipOrder(String orderId) {
        Order order = repository.findById(orderId);
        if (order == null) {
            throw new OrderNotFoundException(orderId);
        }
        order.setStatus(OrderStatus.SHIPPED);
        repository.save(order);
        notifier.notifyOrderShipped(order);
    }

    /**
     * Cancel a pending order; already-shipped orders cannot be cancelled.
     */
    public void cancelOrder(String orderId) {
        Order order = repository.findById(orderId);
        if (order == null) {
            throw new OrderNotFoundException(orderId);
        }
        if (order.getStatus() == OrderStatus.SHIPPED) {
            throw new IllegalStateException("order " + orderId + " has already shipped");
        }
        order.setStatus(OrderStatus.CANCELLED);
        repository.save(order);
    }
}

interface OrderRepository {
    Order findById(String orderId);
    void save(Order order);
}

enum OrderStatus {
    PENDING,
    SHIPPED,
    CANCELLED,
}

class OrderNotFoundException extends RuntimeException {
    OrderNotFoundException(String orderId) {
        super("order " + orderId + " not found");
    }
}
