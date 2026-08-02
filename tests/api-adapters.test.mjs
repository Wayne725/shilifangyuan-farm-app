import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeAdminActivityRegistrationRead,
  normalizeActivityRead,
  normalizeMealEventRead,
  normalizeMealRead,
  normalizeMemberProposalCommentRead,
  normalizeMemberProposalNamedVoteRead,
  normalizeMemberProposalRead,
  normalizeMembershipApplicationRead,
  normalizeMembershipChargeRead,
  normalizeMembershipRead,
  normalizeOrderRead,
  normalizeProductRead,
} from "../src/services/adapters.ts";

test("管理端活動名單與餐點 DTO 會保留操作所需欄位", () => {
  const registration = normalizeAdminActivityRegistrationRead({
    id: "registration-1",
    user_id: "member-1",
    display_name: "林社員",
    email: "member@example.com",
    status: "registered",
    queue_position: 3,
    registered_at: "2026-08-01T00:00:00Z",
  });
  assert.equal(registration.display_name, "林社員");
  assert.equal(registration.email, "member@example.com");
  assert.equal(registration.status, "registered");

  const meal = normalizeMealRead({
    id: "meal-1",
    name: "季節蔬食便當",
    description: "三菜一飯",
    price: 120,
    tax_type: "taxable",
    is_active: true,
  });
  assert.equal(meal.name, "季節蔬食便當");
  assert.equal(meal.price, 120);
  assert.equal(meal.tax_type, "taxable");
});

test("商品物流欄位會從後端 canonical DTO 轉成 App model", () => {
  const product = normalizeProductRead({
    id: "p1",
    name: "冷藏蔬菜",
    description: "",
    category: "當季蔬果",
    unit: "份",
    member_price: 90,
    nonmember_price: 100,
    stock_quantity: 7,
    tax_type: "tax_exempt",
    can_ship: true,
    shipping_temperature: "chilled",
    allowed_shipping_channels: ["home_delivery"],
    is_active: true,
  });
  assert.equal(product.is_shippable, true);
  assert.equal(product.temperature_zone, "chilled");
  assert.deepEqual(product.allowed_logistics, ["home_delivery"]);
  assert.equal(product.stock, 7);
});

test("社員提案與公開互動保留後端提供的實名顯示", () => {
  const proposal = normalizeMemberProposalRead({
    id: "proposal-1",
    title: "共同採購設備",
    body: "討論合作社公用設備",
    status: "voting",
    created_by_name: "林社員",
    minimum_voters: 10,
    tally: { yes: 4, no: 1, abstain: 2 },
    my_vote: "yes",
  });
  assert.equal(proposal.created_by_name, "林社員");

  const comment = normalizeMemberProposalCommentRead({
    id: "comment-1",
    user_id: "user-2",
    display_name: "陳社員",
    body: "我願意協助比價",
    created_at: "2026-08-01T01:00:00Z",
  });
  assert.equal(comment.author_name, "陳社員");

  const vote = normalizeMemberProposalNamedVoteRead({
    user_id: "user-3",
    display_name: "王社員",
    choice: "abstain",
    updated_at: "2026-08-01T02:00:00Z",
  });
  assert.equal(vote.display_name, "王社員");
  assert.equal(vote.choice, "abstain");
});

test("訂單 fulfillment 與 shipment 欄位會正規化", () => {
  const order = normalizeOrderRead({
    id: "o1",
    order_number: "SLF1",
    order_kind: "regular",
    fulfillment_status: "preparing",
    payment_status: "paid",
    invoice_status: "not_eligible",
    membership_type_snapshot: "member",
    amount_total: 270,
    created_at: "2026-08-01T00:00:00Z",
    available_actions: [],
    items: [],
    fulfillment: {
      method: "ecpay_logistics",
      status: "shipped",
      pickup_location: null,
    },
    shipment: {
      id: "s1",
      channel: "home_delivery",
      temperature: "chilled",
      status: "in_transit",
      shipping_fee: 160,
      tracking_number: "T1",
    },
  });
  assert.equal(order.shipment?.logistics_provider, "home_delivery");
  assert.equal(order.shipment?.temperature_zone, "chilled");
  assert.equal(order.fulfillment?.status, "shipped");
});

test("便當銷售通路優先於相容用 order_kind", () => {
  const order = normalizeOrderRead({
    id: "meal-order-1",
    order_number: "MEAL001",
    order_kind: "regular",
    sales_channel: "meal_preorder",
    fulfillment_status: "pending_confirmation",
    payment_status: "paid",
    invoice_status: "not_eligible",
    membership_type_snapshot: "member",
    amount_total: 120,
    created_at: "2026-08-01T00:00:00Z",
    available_actions: ["view"],
    items: [],
  });

  assert.equal(order.order_kind, "meal_preorder");
});

test("社員 wrapper、申請、費用與活動 DTO 都能轉成畫面模型", () => {
  const membership = normalizeMembershipRead({
    membership_type: "member",
    membership: { id: "m1", member_number: "SLF-2026-0001", status: "active" },
    directory: { is_public: true, nickname: "小林", expertise: "種植" },
  });
  assert.equal(membership?.status, "active");
  assert.equal(membership?.nickname, "小林");
  assert.equal(
    normalizeMembershipRead({
      membership_type: "nonmember",
      membership: null,
      directory: null,
    }),
    null,
  );

  const application = normalizeMembershipApplicationRead({
    id: "a1",
    user_id: "user-a1",
    status: "needs_supplement",
    review_reason: "請補件",
    profile: {
      legal_name: "林小美",
      phone: "0912345678",
      birth_date: "1990-01-01",
      address: "高雄市",
      emergency_contact: "林大明｜0987654321",
    },
    documents: [
      { document_type: "id_front", status: "confirmed" },
      { document_type: "id_back", status: "pending_upload" },
    ],
  });
  assert.equal(application.status, "needs_revision");
  assert.equal(application.user_id, "user-a1");
  assert.equal(application.emergency_contact_phone, "0987654321");
  assert.deepEqual(application.confirmed_documents, ["id_front"]);

  const charge = normalizeMembershipChargeRead({
    id: "c1",
    charge_kind: "admission_fee",
    amount: 500,
    status: "pending",
  });
  assert.equal(charge.charge_type, "joining_fee");
  assert.equal(charge.payment_status, "pending");

  const activity = normalizeActivityRead({
    id: "act1",
    title: "健行",
    description: "",
    location: "壽山",
    starts_at: "2026-08-10T00:00:00Z",
    ends_at: "2026-08-10T03:00:00Z",
    registration_deadline: "2026-08-09T00:00:00Z",
    capacity: 10,
    registration_count: 8,
    waitlist_count: 1,
    status: "published",
    my_registration: { status: "registered" },
  });
  assert.equal(activity.venue_name, "壽山");
  assert.equal(activity.registered_count, 8);
  assert.equal(activity.my_registration_status, "registered");
});

test("便當 offering DTO 會保留 offering id 供下單", () => {
  const event = normalizeMealEventRead({
    id: "e1",
    title: "校園午餐",
    location: "第一教學大樓",
    ordering_starts_at: "2026-08-01T00:00:00Z",
    ordering_ends_at: "2026-08-02T00:00:00Z",
    pickup_starts_at: "2026-08-03T04:00:00Z",
    pickup_ends_at: "2026-08-03T05:00:00Z",
    status: "published",
    offerings: [
      {
        id: "offering-1",
        meal_id: "meal-1",
        price: 110,
        capacity: 20,
        reserved_quantity: 3,
        paid_quantity: 2,
        meal: { id: "meal-1", name: "蔬食便當", description: "五菜一飯" },
      },
    ],
  });
  assert.equal(event.venue_name, "第一教學大樓");
  assert.equal(event.items[0].offering_id, "offering-1");
  assert.equal(event.items[0].available_quantity, 17);
});
