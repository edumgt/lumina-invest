import type { Request, Response, NextFunction } from "express";
import { getUserRoles } from "../services/rbac.service";

export function attachRoles() {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    if (!req.session?.user?.id) return next();
    try {
      const roles = await getUserRoles(req.session.user.id);
      req.session.user.roles = roles;
      next();
    } catch (e) {
      next(e);
    }
  };
}

export function requireAdmin(req: Request, res: Response, next: NextFunction): void {
  const roles = req.session?.user?.roles || [];
  if (!roles.includes("admin")) {
    res.status(403).json({ error: "admin required" });
    return;
  }
  next();
}
