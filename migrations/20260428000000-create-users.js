/**
 * Migration: users 컬렉션 생성 및 인덱스 설정
 */
module.exports = {
  async up(db) {
    const collections = await db.listCollections({ name: "users" }).toArray();
    if (collections.length === 0) {
      await db.createCollection("users");
    }
    await db.collection("users").createIndex({ email: 1 }, { unique: true });
    await db.collection("users").createIndex({ clientId: 1 }, { unique: true });
    await db.collection("users").createIndex({ createdAt: -1 });
  },

  async down(db) {
    await db.collection("users").dropIndexes();
    await db.collection("users").drop();
  },
};
