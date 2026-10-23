import assert from "node:assert/strict";
import test from "node:test";
import { filterOrders } from "../web/src/lib/order-search.ts";

const meals = [
  { id: "a", order_number: "M261023ABC", payment_status: "paid", meal_event_title: "午餐", venue_name: "清華大學", items: [{ meal_name: "嫩煮雞胸" }] },
  { id: "b", order_number: "M261024DEF", payment_status: "pending", meal_event_title: "晚餐", venue_name: "合作社", items: [{ meal_name: "雞肉沙拉" }] },
];
test("餐點訂單可依編號、名稱、取餐點及付款狀態交集查詢", () => {
  assert.deepEqual(filterOrders(meals, "ｍ２６１０２３ａｂｃ", "").map((order) => order.id), ["a"]);
  assert.deepEqual(filterOrders(meals, "雞 清華", "paid").map((order) => order.id), ["a"]);
  assert.deepEqual(filterOrders(meals, "雞", "pending").map((order) => order.id), ["b"]);
  assert.deepEqual(filterOrders(meals, "雞", "refunded"), []);
  assert.deepEqual(filterOrders(meals, "  ", ""), meals);
});
test("一般訂單可查商品與地點，特殊字元視為一般文字", () => {
  const orders = [{ id: "c", order_number: "SLF123", payment_status: "paid", fulfillment: { pickup_location: "合作社" }, items: [{ product_name: "蔭油（無糖）" }] }];
  assert.equal(filterOrders(orders, "蔭油 合作社", "paid").length, 1);
  assert.equal(filterOrders(orders, "[.*", "").length, 0);
  assert.equal(filterOrders(orders, "slf123", "").length, 1);
});
