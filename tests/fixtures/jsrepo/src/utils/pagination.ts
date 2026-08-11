import { MAX_PAGE_SIZE } from "../models";

export function paginate<T>(items: T[], page = 1, perPage = 20): T[] {
  const size = Math.min(perPage, MAX_PAGE_SIZE);
  return items.slice((page - 1) * size, page * size);
}
