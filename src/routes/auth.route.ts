import express from "express";
import bcrypt from "bcryptjs";
import { randomBytes } from "crypto";
import { User } from "../models/user.model";

function normalizeEmail(email: string): string {
  return String(email || "").trim().toLowerCase();
}

function isAdminEmail(email: string): boolean {
  const list = String(process.env.ADMIN_EMAILS || "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  return list.includes(String(email || "").toLowerCase());
}

function generateClientId(): string {
  return "C_" + randomBytes(9).toString("base64url");
}

export function createAuthRouter(): express.Router {
  const router = express.Router();

  router.post("/register", async (req, res) => {
    try {
      const name  = String(req.body?.name || "").trim();
      const email = normalizeEmail(req.body?.email);
      const password = String(req.body?.password || "");

      if (!name || !email || password.length < 4) {
        return res.status(400).json({ error: "invalid input" });
      }

      const exists = await User.exists({ email });
      if (exists) return res.status(409).json({ error: "email already exists" });

      const passwordHash = bcrypt.hashSync(password, 10);
      const clientId  = generateClientId();
      const roles     = isAdminEmail(email) ? ["user", "admin"] : ["user"];
      const primaryRole = isAdminEmail(email) ? "admin" : "user";

      const user = await User.create({ name, email, passwordHash, clientId, primaryRole, roles });
      res.json({ ok: true, clientId: user.clientId });
    } catch (e) {
      res.status(500).json({ error: (e as Error).message });
    }
  });

  router.post("/login", async (req, res) => {
    try {
      const email    = normalizeEmail(req.body?.email);
      const password = String(req.body?.password || "");

      const user = await User.findOne({ email });
      if (!user) return res.status(401).json({ error: "invalid credentials" });

      const ok = bcrypt.compareSync(password, user.passwordHash);
      if (!ok) return res.status(401).json({ error: "invalid credentials" });

      req.session.user = {
        id:       user._id.toString(),
        name:     user.name,
        email:    user.email,
        clientId: user.clientId,
        roles:    user.roles,
      };
      res.json({ ok: true, user: req.session.user });
    } catch (e) {
      res.status(500).json({ error: (e as Error).message });
    }
  });

  router.post("/logout", (req, res) => {
    req.session.destroy(() => {
      res.clearCookie("lawrag.sid");
      res.json({ ok: true });
    });
  });

  return router;
}
