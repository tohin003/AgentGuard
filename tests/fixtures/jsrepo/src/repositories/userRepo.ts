import { User } from "../models";

export class UserRepository {
  constructor(private db: unknown) {}

  async getById(id: number): Promise<User | null> {
    return null;
  }

  async listAll(limit = 50): Promise<User[]> {
    return [];
  }
}
