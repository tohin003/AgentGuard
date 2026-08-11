import { UserRepository } from "../repositories/userRepo";
import { paginate } from "../utils/pagination";

export async function listUsers(db: unknown, page = 1) {
  const repo = new UserRepository(db);
  return paginate(await repo.listAll(), page);
}
