import mongoose from "mongoose";
import path from "path";

export async function connectMongo(): Promise<void> {
  const uri = process.env.MONGO_URI || "mongodb://localhost:27017/law_rag";
  await mongoose.connect(uri);
  console.log("[mongo] connected:", mongoose.connection.host);
  await runMigrations();
}

async function runMigrations(): Promise<void> {
  // migrate-mongo는 CommonJS require로 로드
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const migrateMongo = require("migrate-mongo");

  const configPath = path.resolve(process.cwd(), "migrate-mongo-config.js");
  migrateMongo.config.set(require(configPath));

  const { db, client } = await migrateMongo.database.connect();
  const migrated: string[] = await migrateMongo.up(db, client);
  if (migrated.length > 0) {
    console.log("[migrate-mongo] applied:", migrated);
  } else {
    console.log("[migrate-mongo] all up to date");
  }
  await client.close();
}
