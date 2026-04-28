import { createClient } from "redis";

export const redisClient = createClient({
  url: process.env.REDIS_URL || "redis://localhost:6379",
});

redisClient.on("error", (err) => console.error("[redis] error:", err));

export async function connectRedis(): Promise<void> {
  await redisClient.connect();
  console.log("[redis] connected");
}
