package com.example.storefront.notifications;

import java.util.List;
import java.util.function.Consumer;

/**
 * Fans a notification event out to every registered channel.
 */
public class NotificationDispatcher implements Notifier {

    private static final int MAX_RETRY_ATTEMPTS = 3;

    private final List<NotificationChannel> channels;

    /**
     * A default failure handler used when a channel doesn't supply its own.
     */
    private final Consumer<Exception> defaultFailureHandler = (ex) -> {
        System.err.println("notification dispatch failed: " + ex.getMessage());
    };

    public NotificationDispatcher(List<NotificationChannel> channels) {
        this.channels = channels;
    }

    /**
     * Send the given event to every registered channel, retrying transient
     * failures up to MAX_RETRY_ATTEMPTS times per channel.
     */
    @Override
    public void dispatch(NotificationEvent event) {
        for (NotificationChannel channel : channels) {
            int attempt = 0;
            boolean sent = false;
            while (!sent && attempt < MAX_RETRY_ATTEMPTS) {
                try {
                    channel.send(event);
                    sent = true;
                } catch (Exception ex) {
                    attempt++;
                    if (attempt >= MAX_RETRY_ATTEMPTS) {
                        defaultFailureHandler.accept(ex);
                    }
                }
            }
        }
    }

    @Override
    public void notifyOrderCreated(Object order) {
        dispatch(new NotificationEvent("order.created", order));
    }

    @Override
    public void notifyOrderShipped(Object order) {
        dispatch(new NotificationEvent("order.shipped", order));
    }
}

interface Notifier {
    void dispatch(NotificationEvent event);
    void notifyOrderCreated(Object order);
    void notifyOrderShipped(Object order);
}

interface NotificationChannel {
    void send(NotificationEvent event) throws Exception;
}

class NotificationEvent {
    private final String type;
    private final Object payload;

    NotificationEvent(String type, Object payload) {
        this.type = type;
        this.payload = payload;
    }

    String getType() {
        return type;
    }

    Object getPayload() {
        return payload;
    }
}
