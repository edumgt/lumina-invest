import { User } from "../models/user.model";

// ── MongoDB 기반 (Auth/RBAC) ──────────────────────────────

export async function getUserRoles(userId: string): Promise<string[]> {
  const user = await User.findById(userId).select("roles").lean();
  return user?.roles ?? ["user"];
}

export async function ensureUserRole(userId: string, roleName: string): Promise<void> {
  await User.updateOne({ _id: userId }, { $addToSet: { roles: roleName } });
}

export function isAdmin(roles: string[]): boolean {
  return roles.includes("admin");
}
