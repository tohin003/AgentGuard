export interface User {
  id: number;
  email: string;
}

export type OrderStatus = "pending" | "shipped";

export const MAX_PAGE_SIZE = 100;
