import mongoose from "mongoose";

const userSchema = new mongoose.Schema(
  {
    name:         { type: String, required: true },
    email:        { type: String, required: true, unique: true, lowercase: true, trim: true },
    passwordHash: { type: String, required: true },
    clientId:     { type: String, required: true, unique: true },
    primaryRole:  { type: String, default: "user" },
    roles:        { type: [String], default: ["user"] },
  },
  { timestamps: true }
);

export const User = mongoose.model("User", userSchema);
export type UserDoc = mongoose.InferSchemaType<typeof userSchema> & {
  _id: mongoose.Types.ObjectId;
};
