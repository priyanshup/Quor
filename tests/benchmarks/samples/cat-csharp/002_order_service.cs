namespace Storefront.Orders;

using System;
using System.Collections.Generic;

/// <summary>
/// Possible lifecycle states for a customer order.
/// </summary>
public enum OrderStatus
{
    Pending,
    Shipped,
    Cancelled,
}

public interface IOrderRepository
{
    Order FindById(string orderId);
    void Save(Order order);
}

/// <summary>
/// Creates, ships, and cancels customer orders.
/// </summary>
public class OrderService : IOrderService
{
    private readonly IOrderRepository _repository;
    private readonly INotifier _notifier;

    public OrderService(IOrderRepository repository, INotifier notifier)
    {
        _repository = repository;
        _notifier = notifier;
    }

    /// <summary>
    /// Create a new order for the given customer and line items.
    /// </summary>
    public Order CreateOrder(string customerId, List<LineItem> items)
    {
        if (items.Count == 0)
        {
            throw new ArgumentException("order must contain at least one item");
        }
        var order = new Order(customerId, items, OrderStatus.Pending);
        _repository.Save(order);
        _notifier.NotifyOrderCreated(order);
        return order;
    }

    /// <summary>
    /// Mark an order as shipped, notifying the customer.
    /// </summary>
    public void ShipOrder(string orderId)
    {
        var order = _repository.FindById(orderId);
        if (order == null)
        {
            throw new OrderNotFoundException(orderId);
        }
        order.Status = OrderStatus.Shipped;
        _repository.Save(order);
        _notifier.NotifyOrderShipped(order);
    }

    /// <summary>
    /// Cancel a pending order; already-shipped orders cannot be cancelled.
    /// </summary>
    public void CancelOrder(string orderId)
    {
        var order = _repository.FindById(orderId);
        if (order == null)
        {
            throw new OrderNotFoundException(orderId);
        }
        if (order.Status == OrderStatus.Shipped)
        {
            throw new InvalidOperationException($"order {orderId} has already shipped");
        }
        order.Status = OrderStatus.Cancelled;
        _repository.Save(order);
    }
}

public class OrderNotFoundException : Exception
{
    public OrderNotFoundException(string orderId)
        : base($"order {orderId} not found")
    {
    }
}
