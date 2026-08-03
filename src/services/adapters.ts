import type {
  AdminActivityRegistration,
  GroupCampaign,
  LogisticsSelection,
  Meal,
  MealEvent,
  MealOrder,
  MemberActivity,
  MemberDirectoryEntry,
  MemberProposal,
  MemberProposalComment,
  MemberProposalNamedVote,
  Membership,
  MembershipApplication,
  MembershipCharge,
  MembershipDocumentRead,
  Order,
  OrderFulfillment,
  Product,
  Shipment,
} from "../types";

type JsonObject = Record<string, unknown>;

function object(value: unknown): JsonObject {
  return value !== null && typeof value === "object" ? (value as JsonObject) : {};
}

function text(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function normalizeProductRead(value: unknown): Product {
  const product = object(value);
  return {
    ...(product as Product),
    id: text(product.id),
    name: text(product.name),
    description: text(product.description),
    category: product.category as Product["category"],
    unit: text(product.unit),
    member_price: number(product.member_price),
    nonmember_price: number(product.nonmember_price),
    tax_type: product.tax_type as Product["tax_type"],
    stock: number(product.stock_quantity, number(product.stock)),
    stock_quantity: number(product.stock_quantity, number(product.stock)),
    is_active: bool(product.is_active, true),
    is_shippable: bool(product.can_ship, bool(product.is_shippable)),
    temperature_zone: (product.shipping_temperature ??
      product.temperature_zone) as Product["temperature_zone"],
    allowed_logistics: array(
      product.allowed_shipping_channels ?? product.allowed_logistics,
    ) as Product["allowed_logistics"],
  };
}

export function normalizeCampaignRead(value: unknown): GroupCampaign {
  const campaign = object(value);
  return {
    ...(campaign as GroupCampaign),
    can_ship: bool(campaign.can_ship),
    temperature_zone: (campaign.shipping_temperature ??
      campaign.temperature_zone) as GroupCampaign["temperature_zone"],
    allowed_logistics: array(
      campaign.allowed_shipping_channels ?? campaign.allowed_logistics,
    ) as GroupCampaign["allowed_logistics"],
  };
}

export function normalizeShipmentRead(value: unknown): Shipment | null {
  if (!value) return null;
  const shipment = object(value);
  return {
    id: text(shipment.id),
    logistics_provider: (shipment.channel ??
      shipment.logistics_provider) as Shipment["logistics_provider"],
    temperature_zone: (shipment.temperature ??
      shipment.temperature_zone) as Shipment["temperature_zone"],
    status: shipment.status as Shipment["status"],
    tracking_number: (shipment.tracking_number ?? null) as string | null,
    ecpay_logistics_id: (shipment.ecpay_logistics_id ?? null) as string | null,
    shipping_fee: number(shipment.shipping_fee),
  };
}

export function normalizeLogisticsSelectionRead(
  value: unknown,
): LogisticsSelection {
  const selection = object(value);
  const shipment = normalizeShipmentRead(selection.shipment);
  if (!shipment) throw new Error("物流選擇結果缺少物流資料");
  return {
    shipment,
    selection_url: text(selection.selection_url),
    shipping_fee: number(selection.shipping_fee),
    product_subtotal: number(selection.product_subtotal),
    amount_total: number(selection.amount_total),
    expires_in_seconds: number(selection.expires_in_seconds),
  };
}

export function normalizeOrderRead(value: unknown): Order {
  const source = object(value);
  const fulfillmentSource = object(source.fulfillment);
  const nestedStatus = text(fulfillmentSource.status);
  const legacyStatus =
    text(source.fulfillment_status) ||
    ({
      ready: "ready_for_pickup",
      delivered: "picked_up",
      shipped: "preparing",
      awaiting_shipment: "preparing",
      no_show: "cancelled",
      pending: "pending_confirmation",
    }[nestedStatus] ?? nestedStatus) ||
    "pending_confirmation";
  const mealEventSource = object(source.meal_event);
  return {
    ...(source as Order),
    order_kind: (source.sales_channel ?? source.order_kind ?? "regular") as Order["order_kind"],
    fulfillment_status: legacyStatus as Order["fulfillment_status"],
    fulfillment: source.fulfillment
      ? {
          method: (fulfillmentSource.method ??
            source.fulfillment_method) as OrderFulfillment["method"],
          status: fulfillmentSource.status as OrderFulfillment["status"],
          venue_name: (fulfillmentSource.pickup_location ??
            fulfillmentSource.venue_name ??
            null) as string | null,
          pickup_start: (fulfillmentSource.pickup_starts_at ??
            fulfillmentSource.pickup_start ??
            null) as string | null,
          pickup_end: (fulfillmentSource.pickup_ends_at ??
            fulfillmentSource.pickup_end ??
            null) as string | null,
          address_summary: (fulfillmentSource.address_summary ?? null) as
            | string
            | null,
        }
      : undefined,
    shipment: normalizeShipmentRead(source.shipment),
    meal_event: source.meal_event
      ? {
          id: text(mealEventSource.id),
          title: text(mealEventSource.title),
          venue_name: text(
            mealEventSource.location ?? mealEventSource.venue_name,
          ),
          pickup_start: text(
            mealEventSource.pickup_starts_at ?? mealEventSource.pickup_start,
          ),
          pickup_end: text(
            mealEventSource.pickup_ends_at ?? mealEventSource.pickup_end,
          ),
        }
      : null,
  };
}

export function normalizeMembershipRead(value: unknown): Membership | null {
  const envelope = object(value);
  const rawMembership = Object.prototype.hasOwnProperty.call(
    envelope,
    "membership",
  )
    ? envelope.membership
    : value;
  if (!rawMembership) return null;
  const membership = object(rawMembership);
  const directory = object(envelope.directory ?? membership.directory);
  return {
    id: text(membership.id),
    user_id: text(membership.user_id) || undefined,
    member_number: (membership.member_number ?? null) as string | null,
    status: membership.status as Membership["status"],
    started_at: (membership.activated_at ?? membership.started_at ?? null) as
      | string
      | null,
    suspended_at: (membership.suspended_at ?? null) as string | null,
    ended_at: (membership.ended_at ?? null) as string | null,
    status_reason: (membership.status_reason ?? null) as string | null,
    directory_visible: bool(
      directory.is_public,
      bool(membership.directory_visible),
    ),
    nickname: text(
      directory.nickname ?? membership.nickname,
      text(membership.member_number, "社員"),
    ),
    avatar_url: (directory.avatar_url ?? membership.avatar_url ?? null) as
      | string
      | null,
    expertise: text(directory.expertise ?? membership.expertise) || null,
    bio: text(directory.bio ?? membership.bio) || null,
  };
}

export function normalizeMembershipApplicationRead(
  value: unknown,
): MembershipApplication {
  const application = object(value);
  const profile = object(application.profile);
  const emergency = text(
    profile.emergency_contact ?? application.emergency_contact,
  );
  const [emergencyName = "", emergencyPhone = ""] = emergency.split("｜", 2);
  const documents = array(application.documents).map(object);
  const status =
    application.status === "needs_supplement"
      ? "needs_revision"
      : application.status;
  return {
    id: text(application.id),
    user_id: text(application.user_id),
    status: status as MembershipApplication["status"],
    legal_name: text(profile.legal_name ?? application.legal_name),
    phone: text(profile.phone ?? application.phone),
    birth_date: text(profile.birth_date ?? application.birth_date),
    address: text(profile.address ?? application.address),
    emergency_contact_name: text(
      profile.emergency_contact_name ??
        application.emergency_contact_name ??
        emergencyName,
    ),
    emergency_contact_phone: text(
      profile.emergency_contact_phone ??
        application.emergency_contact_phone ??
        emergencyPhone,
    ),
    consented_at: (profile.consented_at ?? application.consented_at ?? null) as
      | string
      | null,
    review_note: (application.review_reason ??
      application.review_note ??
      null) as string | null,
    submitted_at: (application.submitted_at ?? null) as string | null,
    required_documents: ["id_front", "id_back", "secondary"],
    confirmed_documents: documents
      .filter((document) => document.status === "confirmed")
      .map((document) => document.document_type) as MembershipApplication["confirmed_documents"],
    documents: documents.map((document) => ({
      id: text(document.id),
      document_type: document.document_type as MembershipDocumentRead["document_type"],
      status: document.status as MembershipDocumentRead["status"],
      checksum_sha256: (document.checksum_sha256 ?? null) as string | null,
    })),
  };
}

export function normalizeMembershipChargeRead(value: unknown): MembershipCharge {
  const charge = object(value);
  const kind = charge.charge_kind ?? charge.charge_type;
  return {
    id: text(charge.id),
    charge_type: (kind === "admission_fee" ? "joining_fee" : kind) as MembershipCharge["charge_type"],
    amount: number(charge.amount),
    payment_status: (charge.status ??
      charge.payment_status) as MembershipCharge["payment_status"],
    receipt_number: (charge.receipt_number ?? null) as string | null,
    paid_at: (charge.paid_at ?? null) as string | null,
  };
}

export function normalizeActivityRead(value: unknown): MemberActivity {
  const activity = object(value);
  const registration = object(activity.my_registration);
  return {
    id: text(activity.id),
    title: text(activity.title),
    description: text(activity.description),
    image_url: (activity.image_url ?? null) as string | null,
    image_key: text(activity.image_key) || undefined,
    venue_name: text(activity.location ?? activity.venue_name),
    starts_at: text(activity.starts_at),
    ends_at: text(activity.ends_at),
    registration_deadline: text(activity.registration_deadline),
    capacity: number(activity.capacity),
    registered_count: number(
      activity.registration_count,
      number(activity.registered_count),
    ),
    waitlist_count: number(activity.waitlist_count),
    status: activity.status as MemberActivity["status"],
    my_registration_status: (registration.status ??
      activity.my_registration_status ??
      null) as MemberActivity["my_registration_status"],
    created_by_name: text(activity.created_by_name, "社員"),
  };
}

export function normalizeAdminActivityRegistrationRead(
  value: unknown,
): AdminActivityRegistration {
  const registration = object(value);
  return {
    id: text(registration.id),
    user_id: text(registration.user_id),
    display_name: text(registration.display_name, "社員"),
    email: text(registration.email),
    status: registration.status as AdminActivityRegistration["status"],
    queue_position: number(registration.queue_position),
    registered_at: text(registration.registered_at),
    cancelled_at: (registration.cancelled_at ?? null) as string | null,
    checked_in_at: (registration.checked_in_at ?? null) as string | null,
  };
}

export function normalizeMemberDirectoryRead(value: unknown): MemberDirectoryEntry {
  const entry = object(value);
  return {
    id: text(entry.user_id ?? entry.id),
    member_number: text(entry.member_number, "社員"),
    is_public: bool(entry.is_public, true),
    nickname: text(entry.nickname, "社員"),
    avatar_url: (entry.avatar_url ?? null) as string | null,
    expertise: text(entry.expertise) || null,
    bio: text(entry.bio) || null,
  };
}

export function normalizeMemberProposalRead(value: unknown): MemberProposal {
  const proposal = object(value);
  const tally = object(proposal.tally);
  const comments = array(proposal.comments).map((entry) => {
    const comment = object(entry);
    return {
      id: text(comment.id),
      author_name: text(comment.author_name, "社員"),
      body: text(comment.body),
      created_at: text(comment.created_at),
    };
  });
  return {
    id: text(proposal.id),
    title: text(proposal.title),
    summary: text(proposal.body ?? proposal.summary),
    proposal_type: (proposal.proposal_type ?? "resolution") as MemberProposal["proposal_type"],
    options: array(proposal.options).map((value) => {
      const option = object(value);
      return { id: text(option.id), label: text(option.label), position: number(option.position), vote_count: number(option.vote_count) };
    }),
    status: proposal.status as MemberProposal["status"],
    created_by_name: text(proposal.created_by_name, "社員"),
    discussion_ends_at: (proposal.discussion_ends_at ?? null) as string | null,
    voting_ends_at: (proposal.voting_ends_at ?? null) as string | null,
    minimum_voters: number(proposal.minimum_voters, 10),
    yes_count: number(tally.yes, number(proposal.yes_count)),
    no_count: number(tally.no, number(proposal.no_count)),
    abstain_count: number(tally.abstain, number(proposal.abstain_count)),
    my_vote: (proposal.my_vote ?? null) as MemberProposal["my_vote"],
    my_option_id: (proposal.my_option_id ?? null) as string | null,
    admin_outcome: (proposal.result_summary ??
      proposal.admin_outcome ??
      null) as string | null,
    comments,
  };
}

export function normalizeMemberProposalCommentRead(
  value: unknown,
): MemberProposalComment {
  const comment = object(value);
  return {
    id: text(comment.id),
    user_id: text(comment.user_id) || undefined,
    author_name: text(comment.display_name ?? comment.author_name, "社員"),
    body: text(comment.body),
    created_at: text(comment.created_at),
  };
}

export function normalizeMemberProposalNamedVoteRead(
  value: unknown,
): MemberProposalNamedVote {
  const vote = object(value);
  return {
    user_id: text(vote.user_id),
    display_name: text(vote.display_name, "社員"),
    choice: (vote.choice ?? null) as MemberProposalNamedVote["choice"],
    option_id: (vote.option_id ?? null) as string | null,
    option_label: (vote.option_label ?? null) as string | null,
    updated_at: text(vote.updated_at),
  };
}

export function normalizeMealEventRead(value: unknown): MealEvent {
  const event = object(value);
  return {
    id: text(event.id),
    title: text(event.title),
    school_name: text(event.school_name ?? event.location ?? event.venue_name),
    venue_name: text(event.location ?? event.venue_name),
    sales_start: text(event.ordering_starts_at ?? event.sales_start),
    order_deadline: text(event.ordering_ends_at ?? event.order_deadline),
    pickup_start: text(event.pickup_starts_at ?? event.pickup_start),
    pickup_end: text(event.pickup_ends_at ?? event.pickup_end),
    status: event.status as MealEvent["status"],
    items: array(event.offerings ?? event.items).map((entry) => {
      const offering = object(entry);
      const meal = object(offering.meal);
      const capacity = number(offering.capacity);
      const reserved = number(offering.reserved_quantity);
      return {
        offering_id: text(offering.id ?? offering.offering_id),
        meal_id: text(meal.id ?? offering.meal_id),
        meal_name: text(meal.name ?? offering.meal_name),
        description: text(meal.description ?? offering.description),
        price: number(offering.price),
        capacity,
        reserved_quantity: reserved,
        paid_quantity: number(offering.paid_quantity),
        available_quantity: number(
          offering.available_quantity,
          Math.max(0, capacity - reserved),
        ),
        image_url: (meal.image_url ?? offering.image_url ?? null) as
          | string
          | null,
        image_key: text(meal.image_key ?? offering.image_key) || undefined,
      };
    }),
  };
}

export function normalizeMealRead(value: unknown): Meal {
  const meal = object(value);
  return {
    id: text(meal.id),
    name: text(meal.name),
    description: text(meal.description),
    price: number(meal.price),
    image_url: (meal.image_url ?? null) as string | null,
    image_key: text(meal.image_key) || undefined,
    tax_type: meal.tax_type as Meal["tax_type"],
    is_active: bool(meal.is_active, true),
  };
}

export function normalizeMealOrderRead(value: unknown): MealOrder {
  const order = object(value);
  return {
    ...(order as MealOrder),
    pickup_code: (order.pickup_code ?? null) as string | null,
    pickup_qr_payload: (order.pickup_qr_payload ?? null) as string | null,
    available_actions: array(order.available_actions) as MealOrder["available_actions"],
    items: array(order.items).map((entry) => {
      const item = object(entry);
      return {
        meal_id: text(item.meal_id ?? item.offering_id),
        meal_name: text(item.meal_name ?? item.product_name ?? item.name),
        quantity: number(item.quantity),
        unit_price: number(item.unit_price),
        subtotal: number(item.subtotal),
      };
    }),
  };
}

export function confirmedMembershipDocuments(
  value: unknown,
): MembershipDocumentRead[] {
  return array(object(value).documents).map((entry) => {
    const document = object(entry);
    return {
      id: text(document.id),
      document_type: document.document_type as MembershipDocumentRead["document_type"],
      status: document.status as MembershipDocumentRead["status"],
      checksum_sha256: (document.checksum_sha256 ?? null) as string | null,
    };
  });
}
