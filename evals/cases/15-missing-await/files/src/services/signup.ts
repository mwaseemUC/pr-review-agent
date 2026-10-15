import { hashPassword } from "../auth/passwords";

export interface User {
  id: string;
  email: string;
  displayName: string;
  createdAt: Date;
}

export interface NewUser {
  email: string;
  passwordHash: string;
  displayName: string;
}

export interface UserRepository {
  findByEmail(email: string): Promise<User | null>;
  insert(user: NewUser): Promise<User>;
}

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

export class EmailTakenError extends Error {
  constructor(email: string) {
    super(`email already registered: ${email}`);
    this.name = "EmailTakenError";
  }
}

export interface SignupInput {
  email: string;
  password: string;
  displayName?: string;
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 12;

export async function registerUser(
  repo: UserRepository,
  input: SignupInput,
): Promise<{ id: string }> {
  const email = input.email.trim().toLowerCase();
  if (!EMAIL_PATTERN.test(email)) {
    throw new ValidationError("invalid email address");
  }
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    throw new ValidationError(`password must be at least ${MIN_PASSWORD_LENGTH} characters`);
  }
  const existing = repo.findByEmail(email);
  if (existing) {
    throw new EmailTakenError(email);
  }
  const passwordHash = await hashPassword(input.password);
  const user = await repo.insert({
    email,
    passwordHash,
    displayName: input.displayName?.trim() || email.split("@")[0],
  });
  return { id: user.id };
}
