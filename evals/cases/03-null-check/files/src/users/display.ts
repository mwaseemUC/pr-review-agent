export interface Profile {
  nickname?: string;
  avatarUrl?: string;
  timezone: string;
}

export interface User {
  id: string;
  email: string;
  profile?: Profile;
  createdAt: string;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export interface UserCard {
  id: string;
  label: string;
  initials: string;
  avatarUrl: string | null;
}

export function displayName(user: User): string {
  const nickname = user.profile.nickname?.trim();
  if (nickname) {
    return nickname;
  }
  return user.email.split("@")[0];
}

export function toUserCard(user: User): UserCard {
  const label = displayName(user);
  return {
    id: user.id,
    label,
    initials: initials(label),
    avatarUrl: user.profile?.avatarUrl ?? null,
  };
}

export function toUserCards(users: User[]): UserCard[] {
  return users
    .map(toUserCard)
    .sort((a, b) => a.label.localeCompare(b.label));
}
