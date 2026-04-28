import "express-session";

declare module "express-session" {
  interface SessionData {
    user?: {
      id: string;       // MongoDB ObjectId string
      name: string;
      email: string;
      clientId: string;
      roles: string[];
    };
  }
}
