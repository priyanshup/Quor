/**
 * Orders controller — NestJS HTTP layer for order creation, retrieval,
 * and cancellation. Decorator-heavy by nature of the framework: class,
 * method, and property decorators on almost every line.
 */

import {
  Controller,
  Get,
  Post,
  Delete,
  Param,
  Body,
  Query,
  UseGuards,
  Injectable,
  Inject,
  HttpCode,
} from "@nestjs/common";
import { IsString, IsInt, IsPositive, IsArray, ValidateNested, IsOptional } from "class-validator";
import { Type } from "class-transformer";
import { AuthGuard } from "../auth/auth.guard";
import { OrdersService } from "./orders.service";

export class LineItemDto {
  @IsString()
  sku: string;

  @IsInt()
  @IsPositive()
  quantity: number;
}

export class CreateOrderDto {
  @IsString()
  customerId: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => LineItemDto)
  items: LineItemDto[];

  @IsString()
  paymentMethodToken: string;

  @IsOptional()
  @IsString()
  discountCode?: string;
}

@Injectable()
export class OrdersMetricsService {
  private createdCount = 0;

  @LogCall()
  recordCreated(): void {
    this.createdCount += 1;
  }

  getCreatedCount(): number {
    return this.createdCount;
  }
}

@Controller("orders")
@UseGuards(AuthGuard)
export class OrdersController {
  constructor(
    @Inject(OrdersService) private readonly ordersService: OrdersService,
    @Inject(OrdersMetricsService) private readonly metrics: OrdersMetricsService,
  ) {}

  @Post()
  @HttpCode(201)
  async create(@Body() dto: CreateOrderDto) {
    const order = await this.ordersService.createOrder({
      customerId: dto.customerId,
      items: dto.items,
      paymentMethodToken: dto.paymentMethodToken,
    });
    this.metrics.recordCreated();
    return order;
  }

  @Get(":id")
  async findOne(@Param("id") id: string) {
    return this.ordersService.getOrderOrThrow(id);
  }

  @Get()
  async list(@Query("customerId") customerId: string, @Query("limit") limit?: string) {
    const parsedLimit = limit ? parseInt(limit, 10) : 20;
    return this.ordersService.listOrdersForCustomer(customerId, parsedLimit);
  }

  @Delete(":id")
  @HttpCode(204)
  async cancel(@Param("id") id: string, @Body("reason") reason: string) {
    await this.ordersService.cancelOrder(id, reason);
  }
}

function LogCall(): MethodDecorator {
  return function (target, propertyKey, descriptor: PropertyDescriptor) {
    const original = descriptor.value;
    descriptor.value = function (...args: unknown[]) {
      return original.apply(this, args);
    };
    return descriptor;
  };
}
