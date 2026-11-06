export interface SignedInUser {
  id: string;
  actor: string;
  display_name: string;
  enabled: boolean;
  owner: boolean;
  roles: string[];
  permissions: string[];
  csrf: string;
}

export interface AuthSession {
  mode: "local" | "microsoft";
  configured: boolean;
  user: SignedInUser | null;
  needs_owner?: boolean;
}

let csrf = "";
export const getCsrf = () => csrf;
export const setCsrf = (value: string) => { csrf = value; };
