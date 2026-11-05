export type Provenance = "user_said" | "assumed" | "missing";

export interface BriefField {
  name: string;
  label: string;
  value: string | number | boolean | null;
  display: string | null;
  provenance: Provenance;
  required: boolean;
}

export interface Brief {
  fields: BriefField[];
  missing: string[];
  assumed: string[];
  ready: boolean;
  nights: number | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
}

export interface LineItem {
  group: "bookings" | "getting_around";
  category: string;
  label: string;
  detail: string;
  low: number;
  likely: number;
  high: number;
  kind: "quote" | "estimate" | "demo";
  source: string;
  as_of: string;
  notes?: string[];
}

export interface Flight {
  id: string;
  airline: string;
  flight_number: string;
  origin: string;
  origin_name: string;
  destination: string;
  destination_name: string;
  depart_label: string;
  arrive_label: string;
  duration_min: number;
  fare_class: string;
  fare: number;
  bags: number;
  bags_detail: string;
}

export interface Leg {
  key: string;
  label: string;
  mode: "rideshare" | "transit" | "drive";
  mode_label: string;
  start_label: string;
  end_label: string;
  minutes: number;
  minutes_low: number;
  minutes_high: number;
  low: number;
  likely: number;
  high: number;
  kind: "quote" | "estimate";
  notes: string[];
}

export interface Plan {
  id: string;
  letter: string;
  tags: string[];
  recommended: boolean;
  title: string;
  flights: { outbound: Flight; return: Flight };
  legs: Leg[];
  items: LineItem[];
  totals: { bookings: number; getting_around: number; likely: number; low: number; high: number };
  door_to_door: {
    outbound_minutes: number;
    return_minutes: number;
    leave_home: string;
    arrive_destination: string;
    leave_destination: string;
    arrive_home: string;
  };
  convenience: { rating: "High" | "Medium" | "Low"; summary: string; door_to_door_out: string; door_to_door_back: string };
  budget: { status: "under" | "tight" | "over"; amount: number; message: string } | null;
  warnings: string[];
  booking_links: { label: string; url: string }[];
}

export interface PlanSet {
  search_id: string;
  checked_at: string;
  data_mode: string;
  candidates_considered: number;
  plans: Plan[];
  comparison: { headline: string; detail: string; recommended: string; cheapest_flights_plan?: string } | null;
  notes: string[];
  relaxed_arrival: boolean;
  brief_version: number;
  is_current: boolean;
}

export interface TripState {
  id: string;
  brief: Brief;
  brief_version: number;
  messages: ChatMessage[];
  plans: PlanSet | null;
  followed_plan: { search_id: string; plan_id: string; title: string; likely: number } | null;
  assistant_mode: "openai" | "offline";
  reply?: string;
  plans_searched?: boolean;
  errors?: string[];
}
