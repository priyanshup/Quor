package com.example.storefront.reports;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.stream.Collectors;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import com.example.storefront.orders.Order;
import com.example.storefront.orders.OrderRepository;
import com.example.storefront.notifications.Notifier;

/**
 * Generates end-of-day sales reports.
 */
public class ReportGenerator {

    private final OrderRepository repository;
    private final Notifier notifier;

    public ReportGenerator(OrderRepository repository, Notifier notifier) {
        this.repository = repository;
        this.notifier = notifier;
    }

    public Map<String, Long> summarize(LocalDate day) {
        List<Order> orders = repository.findByDate(day);
        Map<String, Long> counts = new HashMap<>();
        for (Order order : orders) {
            counts.merge(order.getStatus(), 1L, Long::sum);
        }
        return counts;
    }
}
