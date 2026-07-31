import { Injectable, Logger, NotFoundException } from "@nestjs/common";
import { InjectRepository } from "@nestjs/typeorm";
import { Repository, FindOptionsWhere } from "typeorm";
import { Order, OrderStatus } from "./order.entity";
import { OrderItem } from "./order-item.entity";
import { Customer } from "../customers/customer.entity";
import { NotificationService } from "../notifications/notification.service";
import { formatCurrency, parseCurrency } from "../shared/currency";
import * as dayjs from "dayjs";

const DEFAULT_PAGE_SIZE = 25;

@Injectable()
export class OrdersService {
  private readonly logger = new Logger(OrdersService.name);

  constructor(
    @InjectRepository(Order) private readonly orders: Repository<Order>,
    private readonly notifications: NotificationService,
  ) {}

  async findRecent(customerId: string): Promise<Order[]> {
    const where: FindOptionsWhere<Order> = { customerId };
    const results = await this.orders.find({ where, take: DEFAULT_PAGE_SIZE });
    if (results.length === 0) {
      throw new NotFoundException(`No orders for customer ${customerId}`);
    }
    return results;
  }
}
