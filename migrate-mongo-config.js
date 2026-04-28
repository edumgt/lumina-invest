require("dotenv").config();

module.exports = {
  mongodb: {
    url: process.env.MONGO_URI || "mongodb://localhost:27017",
    databaseName: process.env.MONGO_DB || "law_rag",
    options: { useNewUrlParser: true, useUnifiedTopology: true },
  },
  migrationsDir: "migrations",
  changelogCollectionName: "changelog",
  migrationFileExtension: ".js",
  useFileHash: false,
  moduleSystem: "commonjs",
};
